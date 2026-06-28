import logging
import json
from typing import List, Dict, Any, Optional
from services.service_result import ServiceResult
from services.errors import ForgeError
from core.dependency_injection import container
from core.database import get_db_connection

logger = logging.getLogger(__name__)

class RAGEngine:
    def __init__(self):
        self.embedding_service = container.resolve('embedding_service') if container.has('embedding_service') else None
        self.reranker_service = container.resolve('reranker_service') if container.has('reranker_service') else None

    def chunk_document(self, text: str, chunk_size: int = 1000, overlap: int = 200) -> ServiceResult[List[str]]:
        try:
            chunks = []
            start = 0
            text_length = len(text)
            while start < text_length:
                end = start + chunk_size
                chunks.append(text[start:end])
                start += (chunk_size - overlap)
            return ServiceResult.success(chunks)
        except Exception as e:
            logger.error(f"Error chunking document: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="RAG_CHUNK_ERROR", message=f"Failed to chunk document: {e}"))

    def generate_embeddings(self, texts: List[str]) -> ServiceResult[List[List[float]]]:
        try:
            if not self.embedding_service:
                embeddings = [[0.1] * 1536 for _ in texts]
                return ServiceResult.success(embeddings)
            
            embeddings = self.embedding_service.embed_documents(texts)
            return ServiceResult.success(embeddings)
        except Exception as e:
            logger.error(f"Error generating embeddings: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="RAG_EMBED_ERROR", message=f"Failed to generate embeddings: {e}"))

    def index_document(self, doc_id: str, text: str, metadata: Dict[str, Any] = None) -> ServiceResult[bool]:
        try:
            chunk_res = self.chunk_document(text)
            if not chunk_res.is_success:
                return ServiceResult.fail(chunk_res.error)
            chunks = chunk_res.value
            
            embed_res = self.generate_embeddings(chunks)
            if not embed_res.is_success:
                return ServiceResult.fail(embed_res.error)
            embeddings = embed_res.value
            
            metadata_json = json.dumps(metadata or {})
            
            with get_db_connection() as conn:
                cursor = conn.cursor()
                for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                    chunk_id = f"{doc_id}_chunk_{i}"
                    emb_json = json.dumps(emb)
                    cursor.execute(
                        """
                        INSERT INTO vector_db (chunk_id, doc_id, text_content, embedding, metadata)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(chunk_id) DO UPDATE SET 
                            text_content=excluded.text_content, 
                            embedding=excluded.embedding,
                            metadata=excluded.metadata
                        """,
                        (chunk_id, doc_id, chunk, emb_json, metadata_json)
                    )
                conn.commit()
            return ServiceResult.success(True)
        except Exception as e:
            logger.error(f"Error indexing document: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="RAG_INDEX_ERROR", message=f"Failed to index document: {e}"))

    def retrieve(self, query: str, top_k: int = 5) -> ServiceResult[List[Dict[str, Any]]]:
        try:
            embed_res = self.generate_embeddings([query])
            if not embed_res.is_success:
                return ServiceResult.fail(embed_res.error)
            query_embedding = embed_res.value[0]
            
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT chunk_id, doc_id, text_content, metadata 
                    FROM vector_db 
                    ORDER BY vec_distance(embedding, ?) ASC 
                    LIMIT ?
                    """,
                    (json.dumps(query_embedding), top_k)
                )
                rows = cursor.fetchall()
                results = []
                for row in rows:
                    results.append({
                        "chunk_id": row[0],
                        "doc_id": row[1],
                        "text_content": row[2],
                        "metadata": json.loads(row[3] or '{}')
                    })
            return ServiceResult.success(results)
        except Exception as e:
            logger.error(f"Error retrieving documents: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="RAG_RETRIEVE_ERROR", message=f"Failed to retrieve documents: {e}"))

    def rerank_results(self, query: str, results: List[Dict[str, Any]], top_n: int = 3) -> ServiceResult[List[Dict[str, Any]]]:
        try:
            if not results:
                return ServiceResult.success([])
            
            if not self.reranker_service:
                return ServiceResult.success(results[:top_n])
            
            texts = [r["text_content"] for r in results]
            scores = self.reranker_service.compute_scores(query, texts)
            
            for r, score in zip(results, scores):
                r["rerank_score"] = score
            
            results.sort(key=lambda x: x.get("rerank_score", 0.0), reverse=True)
            return ServiceResult.success(results[:top_n])
        except Exception as e:
            logger.error(f"Error reranking results: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="RAG_RERANK_ERROR", message=f"Failed to rerank results: {e}"))

    def hybrid_search(self, query: str, top_k: int = 5, rerank_top_n: int = 3) -> ServiceResult[List[Dict[str, Any]]]:
        try:
            dense_res = self.retrieve(query, top_k=top_k*2)
            if not dense_res.is_success:
                return ServiceResult.fail(dense_res.error)
            
            sparse_results = []
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT chunk_id, doc_id, text_content, metadata 
                    FROM vector_db 
                    WHERE text_content LIKE ? 
                    LIMIT ?
                    """,
                    (f"%{query}%", top_k*2)
                )
                rows = cursor.fetchall()
                for row in rows:
                    sparse_results.append({
                        "chunk_id": row[0],
                        "doc_id": row[1],
                        "text_content": row[2],
                        "metadata": json.loads(row[3] or '{}')
                    })
            
            combined_dict = {}
            for res in dense_res.value + sparse_results:
                combined_dict[res["chunk_id"]] = res
            combined_results = list(combined_dict.values())
            
            rerank_res = self.rerank_results(query, combined_results, top_n=rerank_top_n)
            if not rerank_res.is_success:
                return ServiceResult.fail(rerank_res.error)
            
            return ServiceResult.success(rerank_res.value)
        except Exception as e:
            logger.error(f"Error in hybrid search: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="RAG_HYBRID_ERROR", message=f"Failed during hybrid search: {e}"))
