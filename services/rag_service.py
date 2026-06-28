import threading
import time
import os
from models import get_db_connection
from services.embedding_service import EmbeddingService
from services.vector_service import MySQLVectorStore
from services.event_bus import event_bus

class RAGService:
    @staticmethod
    def chunk_text(text, chunk_size=500, chunk_overlap=50):
        words = text.split()
        chunks = []
        start = 0
        while start < len(words):
            end = start + chunk_size
            chunk_words = words[start:end]
            chunks.append(" ".join(chunk_words))
            start += (chunk_size - chunk_overlap)
        return chunks

    @staticmethod
    def process_document(doc_id):
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            # 1. Update job to processing
            cursor.execute("UPDATE knowledge_jobs SET status = 'processing', progress_pct = 10 WHERE doc_id = %s", (doc_id,))
            conn.commit()

            # 2. Get document and knowledge base details
            cursor.execute(
                """
                SELECT d.*, k.chunk_size, k.chunk_overlap, k.embedding_model_id, m.model_name, p.name as provider_name
                FROM knowledge_documents d
                JOIN knowledge_bases k ON d.kb_id = k.id
                LEFT JOIN models m ON k.embedding_model_id = m.id
                LEFT JOIN providers p ON m.provider_id = p.id
                WHERE d.id = %s
                """,
                (doc_id,)
            )
            doc = cursor.fetchone()
            if not doc:
                raise Exception("Document not found")

            filepath = doc["filename"]
            content = ""
            if os.path.exists(filepath):
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
            else:
                # Text/PDF simulation
                content = f"Simulated content chunk for RAG document: {doc['filename']}. " * 100
            
            # 3. Chunk text
            chunk_size = doc["chunk_size"] or 500
            chunk_overlap = doc["chunk_overlap"] or 50
            chunks = RAGService.chunk_text(content, chunk_size, chunk_overlap)
            
            # 4. Generate embeddings and save
            vector_store = MySQLVectorStore()
            provider_name = doc["provider_name"] or "local"
            
            cursor.execute("UPDATE knowledge_jobs SET progress_pct = 40 WHERE doc_id = %s", (doc_id,))
            conn.commit()

            total_chunks = len(chunks)
            if total_chunks == 0:
                total_chunks = 1
                chunks = ["Empty document text."]

            for idx, chunk_text in enumerate(chunks):
                vector = EmbeddingService.embed(chunk_text, provider_name=provider_name)
                
                token_count = len(chunk_text) // 4
                vector_store.save_chunk(
                    doc_id=doc_id,
                    chunk_index=idx,
                    chunk_text=chunk_text,
                    page_number=1,
                    token_count=token_count,
                    embedding_vector=vector
                )
                
                progress = 40 + int((idx + 1) / total_chunks * 50)
                cursor.execute("UPDATE knowledge_jobs SET progress_pct = %s WHERE doc_id = %s", (progress, doc_id))
                conn.commit()

            # 5. Completed
            cursor.execute("UPDATE knowledge_jobs SET status = 'completed', progress_pct = 100 WHERE doc_id = %s", (doc_id,))
            conn.commit()
            
            event_bus.publish("KnowledgeIndexed", {"doc_id": doc_id, "kb_id": doc["kb_id"]})
        except Exception as e:
            print(f"[RAGService Error] Failed to process document {doc_id}: {e}")
            cursor.execute(
                "UPDATE knowledge_jobs SET status = 'failed', error_message = %s WHERE doc_id = %s",
                (str(e), doc_id)
            )
            conn.commit()
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def start_worker_thread():
        def worker_loop():
            print("[RAGService Worker] Background worker thread started.")
            while True:
                conn = get_db_connection()
                cursor = conn.cursor(dictionary=True)
                try:
                    cursor.execute("SELECT doc_id FROM knowledge_jobs WHERE status = 'queued' LIMIT 1")
                    row = cursor.fetchone()
                    if row:
                        doc_id = row["doc_id"]
                        print(f"[RAGService Worker] Processing document job: {doc_id}")
                        RAGService.process_document(doc_id)
                except Exception as e:
                    print(f"[RAGService Worker Error] {e}")
                finally:
                    cursor.close()
                    conn.close()
                time.sleep(1.0)
                
        t = threading.Thread(target=worker_loop, daemon=True)
        t.start()
