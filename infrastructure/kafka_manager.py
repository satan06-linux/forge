import os
import json
import logging
import time
import uuid
from typing import Callable, Dict, Any, Optional
from queue import Queue, Empty

from confluent_kafka import Producer, Consumer, KafkaException, KafkaError
from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class KafkaManager:
    """
    Kafka producer/consumer wrappers replacing DAG execution with event topics.
    Provides robust connection pooling and retry logic.
    """
    def __init__(self, bootstrap_servers: str = None, pool_size: int = 5):
        self.bootstrap_servers = bootstrap_servers or os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
        self.pool_size = pool_size
        self._producer_pool: Queue = Queue(maxsize=pool_size)
        self._initialize_pool()

    def _initialize_pool(self):
        try:
            for i in range(self.pool_size):
                producer = Producer({
                    'bootstrap.servers': self.bootstrap_servers,
                    'acks': 'all',
                    'retries': 5,
                    'retry.backoff.ms': 500,
                    'linger.ms': 5,
                    'client.id': f"forge-producer-{uuid.uuid4()}-{i}"
                })
                self._producer_pool.put(producer)
        except Exception as e:
            logger.error(f"Failed to initialize Kafka producer pool: {e}")
            raise ForgeError(f"Kafka initialization failed: {e}")

    def _get_producer(self) -> Producer:
        try:
            return self._producer_pool.get(timeout=5.0)
        except Empty:
            logger.error("Kafka producer pool exhausted")
            raise ForgeError("Kafka producer pool exhausted")

    def _release_producer(self, producer: Producer):
        try:
            self._producer_pool.put(producer, timeout=1.0)
        except Exception as e:
            logger.warning(f"Failed to release producer back to pool: {e}")

    def produce_event(self, topic: str, event_type: str, payload: Dict[str, Any], key: Optional[str] = None) -> ServiceResult[bool]:
        """
        Produces an event to a Kafka topic using the connection pool with retry logic.
        """
        max_attempts = 3
        attempt = 0
        last_error = None
        
        message_value = json.dumps({
            "event_type": event_type,
            "payload": payload,
            "timestamp": time.time(),
            "event_id": str(uuid.uuid4())
        })
        
        while attempt < max_attempts:
            attempt += 1
            producer = None
            try:
                producer = self._get_producer()
                
                delivery_status = {}
                def delivery_report(err, msg):
                    if err is not None:
                        delivery_status['error'] = err
                    else:
                        delivery_status['partition'] = msg.partition()
                
                producer.produce(
                    topic=topic,
                    key=key.encode('utf-8') if key else None,
                    value=message_value.encode('utf-8'),
                    callback=delivery_report
                )
                
                # Flush to wait for delivery and invoke callbacks
                producer.flush(timeout=10.0)
                
                if 'error' in delivery_status:
                    raise KafkaException(delivery_status['error'])
                
                self._release_producer(producer)
                return ServiceResult.success(True)
                
            except KafkaException as e:
                last_error = e
                logger.warning(f"Kafka production failed on attempt {attempt}: {e}")
                if producer:
                    self._release_producer(producer)
                time.sleep(2 ** attempt)
            except ForgeError as e:
                if producer:
                    self._release_producer(producer)
                return ServiceResult.fail(e)
            except Exception as e:
                last_error = e
                logger.error(f"Unexpected error producing event: {e}")
                if producer:
                    self._release_producer(producer)
                time.sleep(2 ** attempt)
                
        return ServiceResult.fail(ForgeError(f"Failed to produce event after {max_attempts} attempts. Last error: {last_error}"))

    def consume_events(self, topic: str, group_id: str, callback: Callable[[Dict[str, Any]], bool], batch_size: int = 10, timeout_ms: int = 5000) -> ServiceResult[int]:
        """
        Consumes events from a topic and processes them using a callback.
        Returns the number of successfully processed events.
        """
        consumer = None
        try:
            consumer = Consumer({
                'bootstrap.servers': self.bootstrap_servers,
                'group.id': group_id,
                'auto.offset.reset': 'earliest',
                'enable.auto.commit': False,
                'client.id': f"forge-consumer-{uuid.uuid4()}"
            })
            
            consumer.subscribe([topic])
            
            processed_count = 0
            
            # Consume up to batch_size messages
            messages = consumer.consume(num_messages=batch_size, timeout=timeout_ms / 1000.0)
            
            if not messages:
                return ServiceResult.success(0)
                
            for msg in messages:
                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    else:
                        raise KafkaException(msg.error())
                
                try:
                    value = json.loads(msg.value().decode('utf-8'))
                    success = callback(value)
                    if success:
                        processed_count += 1
                    else:
                        logger.warning("Consumer callback returned False, stopping batch processing.")
                        break
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to decode message: {e}")
                    # Skip malformed messages
                    continue
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    break
                    
            if processed_count > 0:
                consumer.commit(asynchronous=False)
                
            return ServiceResult.success(processed_count)
            
        except KafkaException as e:
            logger.error(f"Kafka error during consumption: {e}")
            return ServiceResult.fail(ForgeError(f"Kafka consumer error: {e}"))
        except Exception as e:
            logger.error(f"Failed to consume events: {e}")
            return ServiceResult.fail(ForgeError(f"Consumer error: {e}"))
        finally:
            if consumer:
                try:
                    consumer.close()
                except Exception as e:
                    logger.warning(f"Error closing consumer: {e}")
