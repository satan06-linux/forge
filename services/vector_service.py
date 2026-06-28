import struct
from models import get_db_connection

class VectorStore:
    def save_chunk(self, doc_id, chunk_index, chunk_text, page_number, token_count, embedding_vector):
        pass

    def similarity_search(self, kb_id, query_vector, top_k=5):
        pass

class MySQLVectorStore(VectorStore):
    def save_chunk(self, doc_id, chunk_index, chunk_text, page_number, token_count, embedding_vector):
        # Pack float list into a binary blob
        dimensions = len(embedding_vector)
        binary_vector = struct.pack(f"{dimensions}f", *embedding_vector)
        
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO knowledge_chunks (doc_id, chunk_index, chunk_text, page_number, token_count, embedding_vector)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (doc_id, chunk_index, chunk_text, page_number, token_count, binary_vector)
            )
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            print(f"[MySQLVectorStore Save Error] {e}")
            raise e
        finally:
            cursor.close()
            conn.close()

    def similarity_search(self, kb_id, query_vector, top_k=5):
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT c.id, c.doc_id, c.chunk_text, c.page_number, c.embedding_vector
                FROM knowledge_chunks c
                JOIN knowledge_documents d ON c.doc_id = d.id
                WHERE d.kb_id = %s
                """,
                (kb_id,)
            )
            rows = cursor.fetchall()
            
            results = []
            for row in rows:
                binary_vector = row["embedding_vector"]
                if not binary_vector:
                    continue
                try:
                    vector_dim = len(binary_vector) // 4
                    vector = list(struct.unpack(f"{vector_dim}f", binary_vector))
                    
                    # Compute cosine dot product (both query and DB vectors are pre-normalized)
                    dot_product = sum(q * v for q, v in zip(query_vector, vector))
                    results.append((row, dot_product))
                except Exception as ex:
                    print(f"[MySQLVectorStore Unpack Error] {ex}")
            
            results.sort(key=lambda x: x[1], reverse=True)
            
            top_results = []
            for item, score in results[:top_k]:
                top_results.append({
                    "chunk_id": item["id"],
                    "doc_id": item["doc_id"],
                    "text": item["chunk_text"],
                    "page_number": item["page_number"],
                    "score": score
                })
            return top_results
        except Exception as e:
            print(f"[MySQLVectorStore Search Error] {e}")
            return []
        finally:
            cursor.close()
            conn.close()
