#!/usr/bin/env python3
import json
import time
import os
import logging
from typing import List, Dict
from pathlib import Path
from datetime import datetime

# Import mem0 memory system
from mem0 import Memory
from mem0.configs.base import MemoryConfig

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configure API key and base_url
# 从环境变量读取，避免硬编码敏感信息
API_KEY = os.getenv("OPENAI_API_KEY", "")
BASE_URL = os.getenv("OPENAI_BASE_URL", "")


class EvalMem0:
    """Simplified memory system test class"""

    def __init__(self, retrieve_k: int = 10):
        self.retrieve_k = retrieve_k

        # Set environment variables
        os.environ["OPENAI_API_KEY"] = API_KEY
        os.environ["OPENAI_BASE_URL"] = BASE_URL

        # Initialize mem0 memory system
        config = MemoryConfig(
            llm={
                "provider": "openai",
                "config": {
                    "model": "gpt-4o-mini",
                    "api_key": API_KEY,
                    "openai_base_url": BASE_URL,
                }
            },
            embedder={
                "provider": "openai",
                "config": {
                    "model": "text-embedding-3-small",
                    "api_key": API_KEY,
                    "openai_base_url": BASE_URL
                }
            }
        )
        self.memory = Memory(config)

        # Store retrieval results
        self.retrieval_results = {}
        
        # Store mapping from session_identifier to dialogue_turns
        self.session_dialogue_map = {}

    def add_session_memory(self, dialogue_turns: List[Dict], session_identifier: str, user_id: str = "default_user") -> Dict:
        """
        Add entire session as memory
        
        Args:
            dialogue_turns: List of dialogue turns
            session_identifier: Session identifier
            user_id: User ID
            
        Returns:
            Add result
        """
        # Concatenate session content
        session_content = "\n".join([
            f"Speaker {turn['speaker']}: {turn['content']}" 
            for turn in dialogue_turns
        ])
        
        logger.info(f"Adding session memory: {session_identifier} ({len(dialogue_turns)} turns)")
        
        start_time = time.time()
        add_result = self.memory.add(
            session_content, 
            user_id=user_id,
            metadata={"chunk_id": session_identifier, "session_identifier": session_identifier}
        )
        duration = time.time() - start_time
        
        logger.info(f"Successfully added session memory, took {duration:.2f}s")
        
        return add_result

    def retrieve_memories_with_details(self, query: str, user_id: str = "default_user") -> List[Dict]:
        """
        Retrieve relevant memories with detailed information
        
        Args:
            query: Query question
            user_id: User ID
            
        Returns:
            List of retrieval results
        """
        logger.info(f"Retrieving memories: {query[:50]}...")
        
        start_time = time.time()
        search_results = self.memory.search(query=query, user_id=user_id, limit=self.retrieve_k)
        duration = time.time() - start_time
        
        logger.info(f"Retrieval completed, took {duration:.2f}s")
        
        if search_results and "results" in search_results:
            results = []
            for entry in search_results["results"]:
                # Get chunk_id from metadata
                chunk_id = None
                if "metadata" in entry and isinstance(entry["metadata"], dict):
                    chunk_id = entry["metadata"].get("chunk_id") or entry["metadata"].get("session_identifier")
                
                results.append({
                    "memory": entry.get("memory", ""),
                    "score": entry.get("score", 0.0),
                    "chunk_id": chunk_id,
                    "id": entry.get("id", "")
                })
            return results
        return []

    def process_session(self, session_data: Dict, session_idx: int):
        """
        Process a single session
        
        Args:
            session_data: Session data
            session_idx: Session index
        """
        session_identifier = session_data.get('session_identifier') or session_data.get('session_id', f"session_{session_idx}")
        session_name = session_data.get('session_id') or session_identifier
        dialogue_turns = session_data.get('dialogue_turns', [])
        
        logger.info(f"\n{'='*60}")
        logger.info(f"Processing Session {session_idx + 1}: {session_name}")
        logger.info(f"{'='*60}")
        
        # Save to mapping
        self.session_dialogue_map[session_identifier] = dialogue_turns

        # 1. Process all dialogue turns with is_query=true (BEFORE adding session to memory)
        # This ensures retrieval only accesses historical sessions, not the current one
        for turn_idx, turn in enumerate(dialogue_turns):
            if turn.get('is_query', False):
                question = turn.get('content', '').strip()
                
                if not question:
                    logger.warning(f"Turn {turn_idx + 1}: is_query=True but content is empty, skipping")
                    continue
                
                logger.info(f"\nProcessing query Turn {turn_idx + 1}: {question[:80]}...")
                
                # Retrieve memories
                memory_details = self.retrieve_memories_with_details(question)
                
                # Construct ranked_items
                ranked_items = []
                
                # Add memory type items
                for rank, mem_detail in enumerate(memory_details, start=1):
                    ranked_items.append({
                        "res_type": "memory",
                        "chunk_id": mem_detail.get("chunk_id", "unknown"),
                        "content": mem_detail.get("memory", ""),
                        "score": mem_detail.get("score", 0.0),
                        "rank": rank
                    })
                
                # Add chunk type items (deduplicate by chunk_id)
                chunk_dict = {}
                for rank, mem_detail in enumerate(memory_details, start=1):
                    chunk_id = mem_detail.get("chunk_id", "unknown")
                    score = mem_detail.get("score", 0.0)
                    
                    if chunk_id not in chunk_dict:
                        chunk_dict[chunk_id] = {"max_score": score, "min_rank": rank}
                    else:
                        if score > chunk_dict[chunk_id]["max_score"]:
                            chunk_dict[chunk_id]["max_score"] = score
                        if rank < chunk_dict[chunk_id]["min_rank"]:
                            chunk_dict[chunk_id]["min_rank"] = rank
                
                # Create chunk item for each chunk_id
                for chunk_id, chunk_info in chunk_dict.items():
                    chunk_dialogue_turns = self.session_dialogue_map.get(chunk_id, [])
                    
                    chunk_content_parts = []
                    for t in chunk_dialogue_turns:
                        if isinstance(t, dict) and "content" in t:
                            speaker = t.get("speaker", "")
                            content = t.get("content", "")
                            if content:
                                chunk_content_parts.append(f"Speaker {speaker}: {content}")
                    
                    chunk_content = "\n".join(chunk_content_parts) if chunk_content_parts else ""
                    
                    ranked_items.append({
                        "res_type": "chunk",
                        "chunk_id": chunk_id,
                        "content": chunk_content,
                        "score": chunk_info["max_score"],
                        "rank": chunk_info["min_rank"]
                    })
                
                # Sort by score
                ranked_items.sort(key=lambda x: x["score"], reverse=True)
                
                # Save retrieval results
                self.retrieval_results[question] = {
                    "id": session_identifier,
                    "question": question,
                    "ranked_items": ranked_items,
                    "generated_answer": "",
                    "evidence_used": ""
                }
                
                logger.info(f"Retrieved {len(memory_details)} relevant memory fragments")

        # 2. Add session memory AFTER processing queries (for future sessions)
        self.add_session_memory(dialogue_turns, session_identifier)

    def run(self, data_file: str, output_dir: str = "simple_eval_results"):
        """
        Run the test
        
        Args:
            data_file: Data file path
            output_dir: Output directory
        """
        logger.info(f"Starting test: {data_file}")
        
        # Clear all memories
        logger.info("Clearing all memories...")
        try:
            self.memory.delete_all(user_id="default_user")
        except:
            self.memory.reset()
        
        # Load data
        with open(data_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        if not isinstance(data, dict) or 'dialogues' not in data:
            raise ValueError("Invalid data format, expected dict with 'dialogues' key")
        
        all_sessions = data['dialogues']
        logger.info(f"Total {len(all_sessions)} sessions")
        
        # Build session mapping
        for session_data in all_sessions:
            session_identifier = session_data.get('session_identifier') or session_data.get('session_id')
            if session_identifier:
                dialogue_turns = session_data.get('dialogue_turns', [])
                self.session_dialogue_map[session_identifier] = dialogue_turns
        
        # Process each session
        for idx, session_data in enumerate(all_sessions):
            self.process_session(session_data, idx)
        
        # Save retrieval results
        output_path = Path(output_dir)
        output_path.mkdir(exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        result_file = output_path / f"retrieval_results_{timestamp}.json"
        
        with open(result_file, 'w', encoding='utf-8') as f:
            json.dump(self.retrieval_results, f, ensure_ascii=False, indent=2)
        
        logger.info(f"\n{'='*60}")
        logger.info(f"Test completed!")
        logger.info(f"Retrieval results saved to: {result_file}")
        logger.info(f"Total processed {len(all_sessions)} sessions")
        logger.info(f"Total saved {len(self.retrieval_results)} query retrieval results")
        logger.info(f"{'='*60}")


def main():
    """Main function"""
    # Test file
    data_file = "../dataset/Adeleke_Okonjo_dialogues_256k.json"
    
    # Create test instance
    tester = EvalMem0(retrieve_k=20)
    
    # Run test
    tester.run(data_file, output_dir="simple_eval_results")


if __name__ == "__main__":
    main()
