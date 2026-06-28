import logging
import json
import csv
import io
import uuid
import copy
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime

from services.service_result import ServiceResult
from services.errors import ForgeError, NotFoundError, ValidationError

logger = logging.getLogger(__name__)

@dataclass
class DatasetRecord:
    record_id: str
    content: Dict[str, Any]

@dataclass
class Commit:
    commit_id: str
    parent_id: Optional[str]
    timestamp: str
    message: str
    changes: List[Dict[str, Any]]
    dataset_state: Dict[str, DatasetRecord]

@dataclass
class Branch:
    name: str
    head_commit_id: Optional[str]

class DatasetManager:
    """
    Universal import engine and Git-like version control (diffs, branching, rollback) for datasets.
    """
    def __init__(self):
        # dataset_id -> commit_id -> Commit
        self._commits: Dict[str, Dict[str, Commit]] = {}
        # dataset_id -> branch_name -> Branch
        self._branches: Dict[str, Dict[str, Branch]] = {}
        
    def init_dataset_repo(self, dataset_id: str) -> ServiceResult:
        try:
            if dataset_id in self._commits:
                raise ValidationError(f"Repository for dataset {dataset_id} already initialized")
                
            self._commits[dataset_id] = {}
            main_branch = Branch(name="main", head_commit_id=None)
            self._branches[dataset_id] = {"main": main_branch}
            logger.info(f"Initialized dataset repository for {dataset_id}")
            return ServiceResult.ok(data={"dataset_id": dataset_id, "default_branch": "main"})
        except ForgeError as e:
            return ServiceResult.fail(e)
        except Exception as e:
            logger.exception(f"Error initializing repository for {dataset_id}")
            return ServiceResult.fail(ForgeError(str(e)))

    def import_data(
        self, 
        dataset_id: str, 
        branch_name: str, 
        data_content: str, 
        format_type: str, 
        commit_message: str
    ) -> ServiceResult:
        try:
            if dataset_id not in self._branches:
                raise NotFoundError(f"Repository for dataset {dataset_id} not found")
            
            if branch_name not in self._branches[dataset_id]:
                raise NotFoundError(f"Branch '{branch_name}' not found for dataset {dataset_id}")
                
            # Universal parsing
            records = self._parse_content(data_content, format_type)
            
            branch = self._branches[dataset_id][branch_name]
            parent_commit_id = branch.head_commit_id
            
            current_state = {}
            if parent_commit_id:
                parent_commit = self._commits[dataset_id][parent_commit_id]
                current_state = copy.deepcopy(parent_commit.dataset_state)
                
            new_records = {}
            for rec in records:
                rec_id = rec.get("id") or str(uuid.uuid4())
                new_records[rec_id] = DatasetRecord(record_id=rec_id, content=rec)
                
            current_state.update(new_records)
            
            commit_id = str(uuid.uuid4())
            new_commit = Commit(
                commit_id=commit_id,
                parent_id=parent_commit_id,
                timestamp=datetime.utcnow().isoformat(),
                message=commit_message,
                changes=[{"type": "import", "format": format_type, "count": len(new_records)}],
                dataset_state=current_state
            )
            
            self._commits[dataset_id][commit_id] = new_commit
            branch.head_commit_id = commit_id
            
            logger.info(f"Imported {len(new_records)} records into {dataset_id} branch {branch_name}, commit {commit_id}")
            return ServiceResult.ok(data={"commit_id": commit_id, "branch": branch_name, "imported_count": len(new_records)})
        except ForgeError as e:
            return ServiceResult.fail(e)
        except Exception as e:
            logger.exception("Error importing data")
            return ServiceResult.fail(ForgeError(str(e)))

    def _parse_content(self, data_content: str, format_type: str) -> List[Dict[str, Any]]:
        records = []
        try:
            if format_type.lower() == "json":
                records = json.loads(data_content)
                if not isinstance(records, list):
                    records = [records]
            elif format_type.lower() == "jsonl":
                for line in data_content.strip().split("\n"):
                    if line.strip():
                        records.append(json.loads(line))
            elif format_type.lower() == "csv":
                reader = csv.DictReader(io.StringIO(data_content))
                records = list(reader)
            else:
                raise ValidationError(f"Unsupported format type: {format_type}")
        except Exception as e:
            raise ValidationError(f"Failed to parse data as {format_type}: {str(e)}")
            
        return records

    def create_branch(self, dataset_id: str, new_branch_name: str, from_branch_name: str = "main") -> ServiceResult:
        try:
            if dataset_id not in self._branches:
                raise NotFoundError(f"Repository for dataset {dataset_id} not found")
            
            if new_branch_name in self._branches[dataset_id]:
                raise ValidationError(f"Branch '{new_branch_name}' already exists")
                
            if from_branch_name not in self._branches[dataset_id]:
                raise NotFoundError(f"Source branch '{from_branch_name}' not found")
                
            source_branch = self._branches[dataset_id][from_branch_name]
            
            new_branch = Branch(name=new_branch_name, head_commit_id=source_branch.head_commit_id)
            self._branches[dataset_id][new_branch_name] = new_branch
            
            logger.info(f"Created branch {new_branch_name} from {from_branch_name} in dataset {dataset_id}")
            return ServiceResult.ok(data={"branch": new_branch_name, "head_commit_id": new_branch.head_commit_id})
        except ForgeError as e:
            return ServiceResult.fail(e)
        except Exception as e:
            logger.exception("Error creating branch")
            return ServiceResult.fail(ForgeError(str(e)))

    def rollback(self, dataset_id: str, branch_name: str, target_commit_id: str) -> ServiceResult:
        try:
            if dataset_id not in self._branches:
                raise NotFoundError(f"Repository for dataset {dataset_id} not found")
            
            if branch_name not in self._branches[dataset_id]:
                raise NotFoundError(f"Branch '{branch_name}' not found")
                
            if target_commit_id not in self._commits[dataset_id]:
                raise NotFoundError(f"Commit {target_commit_id} not found in repository")
                
            # Perform a hard reset for rollback
            branch = self._branches[dataset_id][branch_name]
            branch.head_commit_id = target_commit_id
            
            logger.info(f"Rolled back branch {branch_name} to commit {target_commit_id} in dataset {dataset_id}")
            return ServiceResult.ok(data={"branch": branch_name, "head_commit_id": target_commit_id})
        except ForgeError as e:
            return ServiceResult.fail(e)
        except Exception as e:
            logger.exception("Error during rollback")
            return ServiceResult.fail(ForgeError(str(e)))

    def get_diff(self, dataset_id: str, commit_id_a: str, commit_id_b: str) -> ServiceResult:
        try:
            if dataset_id not in self._commits:
                raise NotFoundError(f"Repository for dataset {dataset_id} not found")
            
            commits = self._commits[dataset_id]
            if commit_id_a not in commits:
                raise NotFoundError(f"Commit {commit_id_a} not found")
            if commit_id_b not in commits:
                raise NotFoundError(f"Commit {commit_id_b} not found")
                
            state_a = commits[commit_id_a].dataset_state
            state_b = commits[commit_id_b].dataset_state
            
            diff = {
                "added": [],
                "removed": [],
                "modified": []
            }
            
            for rec_id, rec_b in state_b.items():
                if rec_id not in state_a:
                    diff["added"].append(rec_b.content)
                elif state_a[rec_id].content != rec_b.content:
                    diff["modified"].append({"from": state_a[rec_id].content, "to": rec_b.content})
                    
            for rec_id, rec_a in state_a.items():
                if rec_id not in state_b:
                    diff["removed"].append(rec_a.content)
                    
            return ServiceResult.ok(data=diff)
        except ForgeError as e:
            return ServiceResult.fail(e)
        except Exception as e:
            logger.exception("Error calculating diff")
            return ServiceResult.fail(ForgeError(str(e)))

    def get_dataset_state(self, dataset_id: str, branch_name: str = "main") -> ServiceResult:
        try:
            if dataset_id not in self._branches:
                raise NotFoundError(f"Repository for dataset {dataset_id} not found")
                
            if branch_name not in self._branches[dataset_id]:
                raise NotFoundError(f"Branch '{branch_name}' not found")
                
            branch = self._branches[dataset_id][branch_name]
            
            if not branch.head_commit_id:
                return ServiceResult.ok(data=[])
                
            head_commit = self._commits[dataset_id][branch.head_commit_id]
            records = [rec.content for rec in head_commit.dataset_state.values()]
            return ServiceResult.ok(data=records)
        except ForgeError as e:
            return ServiceResult.fail(e)
        except Exception as e:
            logger.exception("Error fetching dataset state")
            return ServiceResult.fail(ForgeError(str(e)))
