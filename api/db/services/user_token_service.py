#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import logging
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional

from api.db.db_models import DB, UserTokenUsage, User
from api.db.services.common_service import CommonService
from api.db.services.user_service import UserService
from api import settings
from peewee import fn


class UserTokenService(CommonService):
    model = UserTokenUsage
    
    @classmethod
    @DB.connection_context()
    def check_token_limit(cls, user_id: Optional[str] = None, llm_type: Optional[str] = None, llm_name: Optional[str] = None, tokens_to_use: int = 0, conversation_id: Optional[str] = None) -> tuple[bool, str]:
        """
        檢查用戶是否可以使用指定數量的 token
        
        Args:
            user_id: 用戶 ID（可選，向後兼容）
            llm_type: LLM 類型 (CHAT, EMBEDDING, etc.)
            llm_name: LLM 模型名稱
            tokens_to_use: 即將使用的 token 數量
            conversation_id: 對話會話 ID（優先使用）
            
        Returns:
            tuple[bool, str]: (是否允許使用, 錯誤消息)
        """
        if not settings.TOKEN_LIMIT_ENABLED:
            return True, ""
            
        # 優先使用 conversation_id，如果沒有則使用 user_id
        identifier = conversation_id or user_id
        if not identifier:
            return True, ""  # 如果都沒有提供，則不限制
        
        # 如果使用 user_id，檢查用戶是否為管理員
        if user_id and not conversation_id:
            success, user = UserService.get_by_id(user_id)
            if success and user and user.is_superuser:
                return True, ""
        
        # 獲取或創建用戶 token 使用記錄
        usage_record = cls._get_or_create_usage_record(user_id, llm_type, llm_name, conversation_id)
        
        # 檢查是否需要重置使用量
        if cls._should_reset_usage(usage_record):
            cls._reset_usage(usage_record)
            
        # 檢查 token 限制
        if usage_record.token_limit > 0:  # 0 表示無限制
            if usage_record.used_tokens + tokens_to_use > usage_record.token_limit:
                return False, f"Token 使用量已達到限制。已使用: {usage_record.used_tokens}, 限制: {usage_record.token_limit}, 嘗試使用: {tokens_to_use}"
                
        return True, ""
    
    @classmethod
    @DB.connection_context()
    def increase_token_usage(cls, user_id: Optional[str] = None, llm_type: Optional[str] = None, llm_name: Optional[str] = None, tokens_used: int = 0, conversation_id: Optional[str] = None) -> bool:
        """
        增加用戶的 token 使用量
        
        Args:
            user_id: 用戶 ID（可選，向後兼容）
            llm_type: LLM 類型
            llm_name: LLM 模型名稱
            tokens_used: 使用的 token 數量
            conversation_id: 對話會話 ID（優先使用）
            
        Returns:
            bool: 是否成功更新
        """
        try:
            # 優先使用 conversation_id，如果沒有則使用 user_id
            identifier = conversation_id or user_id
            if not identifier:
                return True  # 如果都沒有提供，則不記錄使用量
            
            # 獲取或創建用戶 token 使用記錄
            usage_record = cls._get_or_create_usage_record(user_id, llm_type, llm_name, conversation_id)
            
            # 檢查是否需要重置使用量
            if cls._should_reset_usage(usage_record):
                cls._reset_usage(usage_record)
                
            # 更新使用量
            # 構建查詢條件
            where_conditions = [
                cls.model.llm_type == llm_type,
                cls.model.llm_name == llm_name
            ]
            
            if conversation_id:
                where_conditions.append(cls.model.conversation_id == conversation_id)
            else:
                where_conditions.append(cls.model.user_id == user_id)
                
            num = (
                cls.model.update(used_tokens=cls.model.used_tokens + tokens_used)
                .where(*where_conditions)
                .execute()
            )
            
            return num > 0
            
        except Exception as e:
            identifier = conversation_id or user_id
            logging.error(f"Failed to increase token usage for identifier {identifier}: {e}")
            return False
    
    @classmethod
    @DB.connection_context()
    def get_user_token_usage(cls, user_id: str) -> List[Dict]:
        """
        獲取用戶的 token 使用情況
        
        Args:
            user_id: 用戶 ID
            
        Returns:
            List[Dict]: 用戶的 token 使用記錄列表
        """
        try:
            records = cls.model.select().where(cls.model.user_id == user_id).dicts()
            return list(records)
        except Exception as e:
            logging.error(f"Failed to get token usage for user {user_id}: {e}")
            return []
    
    @classmethod
    @DB.connection_context()
    def set_user_token_limit(cls, user_id: str, llm_type: str, llm_name: str, token_limit: int) -> bool:
        """
        設置用戶的 token 限制
        
        Args:
            user_id: 用戶 ID
            llm_type: LLM 類型
            llm_name: LLM 模型名稱
            token_limit: token 限制數量，0 表示無限制
            
        Returns:
            bool: 是否成功設置
        """
        try:
            usage_record = cls._get_or_create_usage_record(user_id, llm_type, llm_name)
            
            num = (
                cls.model.update(token_limit=token_limit)
                .where(
                    cls.model.user_id == user_id,
                    cls.model.llm_type == llm_type,
                    cls.model.llm_name == llm_name
                )
                .execute()
            )
            
            return num > 0
            
        except Exception as e:
            logging.error(f"Failed to set token limit for user {user_id}: {e}")
            return False
    
    @classmethod
    @DB.connection_context()
    def reset_user_token_usage(cls, user_id: str, llm_type: str = None, llm_name: str = None) -> bool:
        """
        重置用戶的 token 使用量
        
        Args:
            user_id: 用戶 ID
            llm_type: LLM 類型 (可選，為空則重置所有類型)
            llm_name: LLM 模型名稱 (可選，為空則重置所有模型)
            
        Returns:
            bool: 是否成功重置
        """
        try:
            query = cls.model.update(
                used_tokens=0,
                reset_date=cls._get_next_reset_date()
            ).where(cls.model.user_id == user_id)
            
            if llm_type:
                query = query.where(cls.model.llm_type == llm_type)
            if llm_name:
                query = query.where(cls.model.llm_name == llm_name)
                
            num = query.execute()
            return num > 0
            
        except Exception as e:
            logging.error(f"Failed to reset token usage for user {user_id}: {e}")
            return False
    
    @classmethod
    @DB.connection_context()
    def get_all_users_token_usage(cls, limit: int = 100, offset: int = 0) -> List[Dict]:
        """
        獲取所有用戶的 token 使用統計 (管理員功能)
        現在包含基於 conversation_id 和 user_id 的記錄
        
        Args:
            limit: 限制返回數量
            offset: 偏移量
            
        Returns:
            List[Dict]: 用戶 token 使用統計列表
        """
        try:
            results = []
            
            # 使用原始 SQL 查詢來避免 Peewee ORM 問題
            cursor = DB.execute_sql("""
                SELECT user_id, conversation_id, llm_type, llm_name, used_tokens, 
                       token_limit, reset_date, is_active, create_time, update_time
                FROM user_token_usage 
                ORDER BY update_time DESC
                LIMIT %s OFFSET %s
            """, (limit * 2, offset))  # 獲取更多記錄以防需要過濾
            
            all_records = cursor.fetchall()
            
            for record in all_records:
                (user_id, conversation_id, llm_type, llm_name, used_tokens, 
                 token_limit, reset_date, is_active, create_time, update_time) = record
                
                # 基於 conversation_id 的記錄
                if conversation_id is not None:
                    # 安全地截取對話ID用於顯示
                    conversation_display = conversation_id[:8] + '...' if len(conversation_id) > 8 else conversation_id
                    
                    results.append({
                        'user_id': conversation_id,  # 使用 conversation_id 作為用戶標識
                        'nickname': f'會話 {conversation_display}',  # 顯示會話ID的前8位或完整ID
                        'user_email': conversation_id,  # 使用完整的 conversation_id
                        'is_superuser': False,  # conversation 用戶不是管理員
                        'llm_type': llm_type,
                        'llm_name': llm_name,
                        'used_tokens': used_tokens,
                        'token_limit': token_limit,
                        'reset_date': reset_date,
                        'is_active': bool(is_active),
                        'create_date': create_time,
                        'update_date': update_time,
                    })
                # 基於 user_id 的記錄（向後兼容）
                elif user_id is not None:
                    # 嘗試獲取用戶信息
                    try:
                        user = User.get(User.id == user_id)
                        nickname = user.nickname
                        email = user.email
                        is_superuser = user.is_superuser
                    except User.DoesNotExist:
                        nickname = f'用戶 {user_id}'
                        email = user_id
                        is_superuser = False
                    
                    results.append({
                        'user_id': user_id,
                        'nickname': nickname,
                        'user_email': email,
                        'is_superuser': is_superuser,
                        'llm_type': llm_type,
                        'llm_name': llm_name,
                        'used_tokens': used_tokens,
                        'token_limit': token_limit,
                        'reset_date': reset_date,
                        'is_active': bool(is_active),
                        'create_date': create_time,
                        'update_date': update_time,
                    })
            
            # 3. 如果沒有任何記錄，顯示系統用戶的預設記錄
            if not results:
                all_users = User.select().where(User.status == "1").order_by(User.id)
                for user in all_users:
                    default_limit = 0 if user.is_superuser else getattr(settings, 'NORMAL_USER_TOKEN_LIMIT', 0)
                    results.append({
                        'user_id': user.id,
                        'nickname': user.nickname,
                        'user_email': user.email,
                        'is_superuser': user.is_superuser,
                        'llm_type': 'CHAT',
                        'llm_name': 'Default',
                        'used_tokens': 0,
                        'token_limit': default_limit,
                        'reset_date': cls._get_next_reset_date(),
                        'is_active': True,
                        'create_date': datetime.now(),
                        'update_date': datetime.now(),
                    })
            
            # 按更新時間排序並應用分頁
            results.sort(key=lambda x: x['update_date'] if x['update_date'] else datetime.min, reverse=True)
            
            # 限制結果數量
            return results[:limit]
            
        except Exception as e:
            logging.error(f"Failed to get all users token usage: {e}", exc_info=True)
            # 如果 SQL 查詢失敗，嘗試檢查表是否存在
            try:
                # 檢查表是否存在
                cursor = DB.execute_sql("SHOW TABLES LIKE 'user_token_usage'")
                tables = cursor.fetchall()
                if not tables:
                    logging.error("user_token_usage table does not exist")
                    # 如果表不存在，返回用戶預設記錄
                    results = []
                    all_users = User.select().where(User.status == "1").order_by(User.id)
                    for user in all_users:
                        default_limit = 0 if user.is_superuser else getattr(settings, 'NORMAL_USER_TOKEN_LIMIT', 0)
                        results.append({
                            'user_id': user.id,
                            'nickname': user.nickname,
                            'user_email': user.email,
                            'is_superuser': user.is_superuser,
                            'llm_type': 'CHAT',
                            'llm_name': 'Default',
                            'used_tokens': 0,
                            'token_limit': default_limit,
                            'reset_date': cls._get_next_reset_date(),
                            'is_active': True,
                            'create_date': datetime.now(),
                            'update_date': datetime.now(),
                        })
                    return results
            except Exception as table_check_error:
                logging.error(f"Failed to check table existence: {table_check_error}")
            
            return []
    
    @classmethod
    @DB.connection_context()
    def get_token_usage_statistics(cls) -> Dict:
        """
        獲取 token 使用統計概覽 (管理員功能)
        
        Returns:
            Dict: 統計信息
        """
        try:
            logging.info("Starting to get token usage statistics")
            
            # 檢查表是否存在
            try:
                cursor = DB.execute_sql("SHOW TABLES LIKE 'user_token_usage'")
                tables = cursor.fetchall()
                if not tables:
                    logging.error("user_token_usage table does not exist")
                    # 如果表不存在，返回基本統計
                    total_users = User.select().count()
                    return {
                        "total_users": total_users,
                        "active_users": 0,
                        "total_tokens_used": 0,
                        "total_tokens_limit": 0,
                        "users_over_limit": 0,
                        "tokens_by_type": {},
                        "statistics_date": datetime.now().isoformat()
                    }
                
                logging.info("user_token_usage table exists and is accessible")
            except Exception as table_error:
                logging.error(f"user_token_usage table issue: {table_error}")
                # 如果表不存在或有問題，嘗試計算基本的用戶統計
                from api.db.services.user_service import UserService
                total_users = User.select().count()
                logging.info(f"Total users from User table: {total_users}")
                return {
                    "total_users": total_users,
                    "active_users": 0,
                    "total_tokens_used": 0,
                    "total_tokens_limit": 0,
                    "users_over_limit": 0,
                    "tokens_by_type": {},
                    "statistics_date": datetime.now().isoformat()
                }
            
            # 總用戶數
            total_users = User.select().count()
            logging.info(f"Total users: {total_users}")
            
            # 使用原始 SQL 查詢來獲取統計數據
            try:
                # 檢查是否有任何 token 使用記錄
                cursor = DB.execute_sql("SELECT COUNT(*) FROM user_token_usage")
                total_records = cursor.fetchone()[0]
                logging.info(f"Total token usage records: {total_records}")
                
                # 活躍用戶數 (有使用過 token 的用戶)
                # 使用原始 SQL 來避免 Peewee 問題
                cursor = DB.execute_sql("""
                    SELECT DISTINCT user_id FROM user_token_usage 
                    WHERE used_tokens > 0 AND user_id IS NOT NULL
                """)
                active_user_ids = set(row[0] for row in cursor.fetchall())
                
                cursor = DB.execute_sql("""
                    SELECT DISTINCT conversation_id FROM user_token_usage 
                    WHERE used_tokens > 0 AND conversation_id IS NOT NULL
                """)
                active_conversation_ids = set(row[0] for row in cursor.fetchall())
                
                active_users_by_user_id = len(active_user_ids)
                active_users_by_conversation_id = len(active_conversation_ids)
                active_users = active_users_by_user_id + active_users_by_conversation_id
                
                logging.info(f"Active users: {active_users} (user_id: {active_users_by_user_id}, conversation_id: {active_users_by_conversation_id})")
                
                # 記錄一些樣本
                if active_user_ids:
                    logging.info(f"Active user samples (user_id): {list(active_user_ids)[:3]}")
                if active_conversation_ids:
                    logging.info(f"Active user samples (conversation_id): {list(active_conversation_ids)[:3]}")
                
                # 總 token 使用量
                cursor = DB.execute_sql("SELECT SUM(used_tokens) FROM user_token_usage")
                total_tokens_used_result = cursor.fetchone()[0]
                total_tokens_used = total_tokens_used_result or 0
                logging.info(f"Total tokens used: {total_tokens_used}")
                
                # 總 token 限制量
                cursor = DB.execute_sql("SELECT SUM(token_limit) FROM user_token_usage WHERE token_limit > 0")
                total_tokens_limit_result = cursor.fetchone()[0]
                total_tokens_limit = total_tokens_limit_result or 0
                logging.info(f"Total tokens limit: {total_tokens_limit}")
                
                # 超過限制的用戶數
                cursor = DB.execute_sql("""
                    SELECT DISTINCT user_id FROM user_token_usage 
                    WHERE token_limit > 0 AND used_tokens >= token_limit AND user_id IS NOT NULL
                """)
                over_limit_user_ids = set(row[0] for row in cursor.fetchall())
                
                cursor = DB.execute_sql("""
                    SELECT DISTINCT conversation_id FROM user_token_usage 
                    WHERE token_limit > 0 AND used_tokens >= token_limit AND conversation_id IS NOT NULL
                """)
                over_limit_conversation_ids = set(row[0] for row in cursor.fetchall())
                
                users_over_limit_by_user_id = len(over_limit_user_ids)
                users_over_limit_by_conversation_id = len(over_limit_conversation_ids)
                users_over_limit = users_over_limit_by_user_id + users_over_limit_by_conversation_id
                
                logging.info(f"Users over limit: {users_over_limit} (user_id: {users_over_limit_by_user_id}, conversation_id: {users_over_limit_by_conversation_id})")
                
                # 按類型統計 token 使用量
                cursor = DB.execute_sql("""
                    SELECT llm_type, SUM(used_tokens) 
                    FROM user_token_usage 
                    GROUP BY llm_type
                """)
                type_stats = {row[0]: row[1] for row in cursor.fetchall()}
                logging.info(f"Type stats: {type_stats}")
                
            except Exception as sql_error:
                logging.error(f"SQL query error: {sql_error}")
                # 使用默認值
                active_users = 0
                total_tokens_used = 0
                total_tokens_limit = 0
                users_over_limit = 0
                type_stats = {}
            
            result = {
                "total_users": total_users,
                "active_users": active_users,
                "total_tokens_used": total_tokens_used,
                "total_tokens_limit": total_tokens_limit,
                "users_over_limit": users_over_limit,
                "tokens_by_type": type_stats,
                "statistics_date": datetime.now().isoformat()
            }
            logging.info(f"Final statistics result: {result}")
            
            return result
            
        except Exception as e:
            logging.error(f"Failed to get token usage statistics: {e}", exc_info=True)
            # 如果統計計算失敗，至少返回用戶總數
            try:
                total_users = User.select().count()
                return {
                    "total_users": total_users,
                    "active_users": 0,
                    "total_tokens_used": 0,
                    "total_tokens_limit": 0,
                    "users_over_limit": 0,
                    "tokens_by_type": {},
                    "statistics_date": datetime.now().isoformat()
                }
            except Exception as fallback_error:
                logging.error(f"Even fallback failed: {fallback_error}")
                return {
                    "total_users": 0,
                    "active_users": 0,
                    "total_tokens_used": 0,
                    "total_tokens_limit": 0,
                    "users_over_limit": 0,
                    "tokens_by_type": {},
                    "statistics_date": datetime.now().isoformat()
                }
    
    @classmethod
    def _get_or_create_usage_record(cls, user_id: Optional[str], llm_type: Optional[str], llm_name: Optional[str], conversation_id: Optional[str] = None) -> UserTokenUsage:
        """
        獲取或創建用戶 token 使用記錄
        """
        try:
            # 構建查詢條件
            where_conditions = [
                cls.model.llm_type == llm_type,
                cls.model.llm_name == llm_name
            ]
            
            if conversation_id:
                where_conditions.append(cls.model.conversation_id == conversation_id)
            else:
                where_conditions.append(cls.model.user_id == user_id)
                
            record = cls.model.get(*where_conditions)
            return record
        except cls.model.DoesNotExist:
            # 創建新記錄
            # 確定默認限制
            default_limit = settings.NORMAL_USER_TOKEN_LIMIT
            
            # 如果使用 user_id 且用戶是管理員，則設置為無限制
            if user_id and not conversation_id:
                success, user = UserService.get_by_id(user_id)
                if success and user and user.is_superuser:
                    default_limit = 0
            
            record_data = {
                "llm_type": llm_type,
                "llm_name": llm_name,
                "used_tokens": 0,
                "token_limit": default_limit,
                "reset_date": cls._get_next_reset_date(),
                "is_active": True
            }
            
            if conversation_id:
                record_data["conversation_id"] = conversation_id
                record_data["user_id"] = None  # 當使用 conversation_id 時，user_id 可以為空
            else:
                record_data["user_id"] = user_id
                record_data["conversation_id"] = None
            
            record = cls.model.create(**record_data)
            return record
    
    @classmethod
    def _should_reset_usage(cls, usage_record: UserTokenUsage) -> bool:
        """
        檢查是否需要重置使用量
        """
        if not usage_record.reset_date:
            return True
            
        return date.today() >= usage_record.reset_date
    
    @classmethod
    def _reset_usage(cls, usage_record: UserTokenUsage):
        """
        重置使用量
        """
        usage_record.used_tokens = 0
        usage_record.reset_date = cls._get_next_reset_date()
        usage_record.save()
    
    @classmethod
    def _get_next_reset_date(cls) -> date:
        """
        獲取下次重置日期
        """
        today = date.today()
        
        if settings.TOKEN_LIMIT_RESET_INTERVAL == 'daily':
            return today + timedelta(days=1)
        elif settings.TOKEN_LIMIT_RESET_INTERVAL == 'weekly':
            days_until_monday = (7 - today.weekday()) % 7
            if days_until_monday == 0:  # 如果今天是週一
                days_until_monday = 7
            return today + timedelta(days=days_until_monday)
        else:  # monthly
            if today.month == 12:
                return date(today.year + 1, 1, 1)
            else:
                return date(today.year, today.month + 1, 1) 