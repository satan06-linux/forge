import os
import shutil
import logging
from datetime import datetime
from typing import Dict, Any, Optional

from services.service_result import ServiceResult
from services.errors import ForgeError, StorageError

logger = logging.getLogger(__name__)

class BackupError(ForgeError):
    default_error_code = "BACKUP_ERROR"
    default_retryable = False

class BackupManager:
    """
    Manages automatic incremental and full backups for datasets, checkpoints,
    models, configs, cards, and training history.
    """
    def __init__(self, root_dir: str, backup_dir: str):
        self.root_dir = os.path.abspath(root_dir)
        self.backup_dir = os.path.abspath(backup_dir)
        
        self.target_dirs = [
            "datasets",
            "checkpoints",
            "models",
            "configs",
            "cards",
            "history"
        ]

    def _ensure_dirs(self) -> None:
        """Creates target directories if they don't exist in root and backup."""
        os.makedirs(self.backup_dir, exist_ok=True)
        for d in self.target_dirs:
            os.makedirs(os.path.join(self.root_dir, d), exist_ok=True)

    def create_full_backup(self, backup_name: Optional[str] = None) -> ServiceResult:
        """
        Creates a complete copy of all targeted directories.
        """
        try:
            self._ensure_dirs()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            b_name = backup_name or f"full_backup_{timestamp}"
            dest_path = os.path.join(self.backup_dir, b_name)
            
            os.makedirs(dest_path, exist_ok=True)
            
            copied_files = 0
            for d in self.target_dirs:
                src = os.path.join(self.root_dir, d)
                dst = os.path.join(dest_path, d)
                if os.path.exists(src) and os.path.isdir(src):
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                    for root, _, files in os.walk(src):
                        copied_files += len(files)
                        
            logger.info(f"Full backup '{b_name}' created successfully with {copied_files} files.")
            return ServiceResult.ok({
                "backup_type": "full",
                "backup_name": b_name,
                "path": dest_path,
                "files_backed_up": copied_files
            })
        except Exception as e:
            logger.error(f"Failed to create full backup: {e}", exc_info=True)
            return ServiceResult.fail(BackupError(f"Full backup failed: {e}"))

    def create_incremental_backup(self, last_backup_time: float, backup_name: Optional[str] = None) -> ServiceResult:
        """
        Copies files from targeted directories that were modified after last_backup_time.
        """
        try:
            self._ensure_dirs()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            b_name = backup_name or f"incremental_backup_{timestamp}"
            dest_path = os.path.join(self.backup_dir, b_name)
            
            copied_files = 0
            
            for d in self.target_dirs:
                src = os.path.join(self.root_dir, d)
                dst = os.path.join(dest_path, d)
                
                if not os.path.exists(src) or not os.path.isdir(src):
                    continue
                    
                for root, _, files in os.walk(src):
                    for file in files:
                        file_path = os.path.join(root, file)
                        mtime = os.path.getmtime(file_path)
                        
                        if mtime > last_backup_time:
                            rel_path = os.path.relpath(root, src)
                            target_dir = os.path.join(dst, rel_path) if rel_path != '.' else dst
                            os.makedirs(target_dir, exist_ok=True)
                            
                            shutil.copy2(file_path, os.path.join(target_dir, file))
                            copied_files += 1
                            
            if copied_files > 0:
                logger.info(f"Incremental backup '{b_name}' created successfully with {copied_files} files.")
            else:
                logger.info("No files modified since last backup time. Incremental backup skipped.")
                # We can remove the empty backup dir
                if os.path.exists(dest_path) and not os.listdir(dest_path):
                    os.rmdir(dest_path)
                
            return ServiceResult.ok({
                "backup_type": "incremental",
                "backup_name": b_name if copied_files > 0 else None,
                "path": dest_path if copied_files > 0 else None,
                "files_backed_up": copied_files,
                "since_timestamp": last_backup_time
            })
        except Exception as e:
            logger.error(f"Failed to create incremental backup: {e}", exc_info=True)
            return ServiceResult.fail(BackupError(f"Incremental backup failed: {e}"))

    def restore_backup(self, backup_name: str) -> ServiceResult:
        """
        Restores a specified backup over the current data.
        """
        try:
            source_path = os.path.join(self.backup_dir, backup_name)
            if not os.path.exists(source_path):
                return ServiceResult.fail(StorageError(f"Backup '{backup_name}' not found at {source_path}"))
                
            restored_files = 0
            for d in self.target_dirs:
                src = os.path.join(source_path, d)
                dst = os.path.join(self.root_dir, d)
                
                if os.path.exists(src) and os.path.isdir(src):
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                    for root, _, files in os.walk(src):
                        restored_files += len(files)
                        
            logger.info(f"Backup '{backup_name}' restored successfully ({restored_files} files).")
            return ServiceResult.ok({
                "action": "restore",
                "backup_name": backup_name,
                "files_restored": restored_files
            })
        except Exception as e:
            logger.error(f"Failed to restore backup '{backup_name}': {e}", exc_info=True)
            return ServiceResult.fail(BackupError(f"Restore failed: {e}"))
            
    def list_backups(self) -> ServiceResult:
        """
        Lists all available backups in the backup directory.
        """
        try:
            if not os.path.exists(self.backup_dir):
                return ServiceResult.ok({"backups": []})
                
            backups = []
            for item in os.listdir(self.backup_dir):
                item_path = os.path.join(self.backup_dir, item)
                if os.path.isdir(item_path):
                    mtime = os.path.getmtime(item_path)
                    backups.append({
                        "name": item,
                        "timestamp": mtime,
                        "created_at": datetime.fromtimestamp(mtime).isoformat()
                    })
                    
            backups.sort(key=lambda x: x["timestamp"], reverse=True)
            return ServiceResult.ok({"backups": backups})
        except Exception as e:
            logger.error(f"Failed to list backups: {e}", exc_info=True)
            return ServiceResult.fail(BackupError(f"List backups failed: {e}"))
