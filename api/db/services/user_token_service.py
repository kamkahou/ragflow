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
            
        # 強制要求 conversation_id
        if not conversation_id:
            logging.error("conversation_id is required for token limit checking")
            return True, ""  # 如果沒有 conversation_id，不限制（向下兼容）
        
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
            # 強制要求 conversation_id
            if not conversation_id:
                logging.error("conversation_id is required for token usage tracking")
                return False
            
            # 獲取或創建用戶 token 使用記錄
            usage_record = cls._get_or_create_usage_record(user_id, llm_type, llm_name, conversation_id)
            
            # 檢查是否需要重置使用量
            if cls._should_reset_usage(usage_record):
                cls._reset_usage(usage_record)
                
            # 更新使用量 - 僅基於 conversation_id
            num = (
                cls.model.update(used_tokens=cls.model.used_tokens + tokens_used)
                .where(
                    cls.model.conversation_id == conversation_id,
                    cls.model.llm_type == llm_type,
                    cls.model.llm_name == llm_name
                )
                .execute()
            )
            
            return num > 0
            
        except Exception as e:
            logging.error(f"Failed to increase token usage for conversation_id {conversation_id}: {e}")
            return False
    
    @classmethod
    @DB.connection_context()
    def get_user_token_usage(cls, user_id: str) -> List[Dict]:
        """
        獲取用戶的 token 使用情況（基於 conversation_id 的聚合）
        
        Args:
            user_id: 用戶 ID
            
        Returns:
            List[Dict]: 用戶的 token 使用記錄列表
        """
        try:
            # 查詢用戶的所有 conversation
            cursor = DB.execute_sql("""
                SELECT id FROM conversation WHERE user_id = %s
                UNION
                SELECT id FROM api_4_conversation WHERE user_id = %s
            """, (user_id, user_id))
            
            conversation_ids = [row[0] for row in cursor.fetchall()]
            
            if not conversation_ids:
                # 如果沒有對話，返回預設的限制信息
                try:
                    from api.db.services.user_service import UserService
                    success, user = UserService.get_by_id(user_id)
                    if success and user:
                        default_limit = 0 if user.is_superuser else getattr(settings, 'NORMAL_USER_TOKEN_LIMIT', 100000)
                        return [{
                            'llm_type': 'CHAT',
                            'llm_name': 'default',
                            'used_tokens': 0,
                            'token_limit': default_limit,
                            'reset_date': cls._get_next_reset_date(),
                            'is_active': True
                        }]
                except Exception:
                    pass
                return []
            
            # 聚合同一用戶在不同 conversation 中的 token 使用量
            placeholders = ','.join(['%s'] * len(conversation_ids))
            cursor = DB.execute_sql(f"""
                SELECT 
                    llm_type,
                    llm_name,
                    SUM(used_tokens) as total_used_tokens,
                    MAX(token_limit) as token_limit,
                    MAX(reset_date) as reset_date,
                    MAX(is_active) as is_active
                FROM user_token_usage 
                WHERE conversation_id IN ({placeholders})
                GROUP BY llm_type, llm_name
            """, conversation_ids)
            
            results = []
            for row in cursor.fetchall():
                llm_type, llm_name, used_tokens, token_limit, reset_date, is_active = row
                results.append({
                    'llm_type': llm_type,
                    'llm_name': llm_name,
                    'used_tokens': used_tokens or 0,
                    'token_limit': token_limit or 0,
                    'reset_date': reset_date,
                    'is_active': bool(is_active)
                })
            
            return results
            
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
                
                # 只處理基於 conversation_id 的記錄
                if conversation_id is not None:
                    # 通過 conversation_id 查找實際用戶信息
                    actual_user_email = None
                    actual_user_nickname = None
                    actual_user_id = None
                    actual_is_superuser = False
                    
                    try:
                        # 首先嘗試從 Conversation 表查找
                        conv_cursor = DB.execute_sql("""
                            SELECT user_id FROM conversation WHERE id = %s
                        """, (conversation_id,))
                        conv_result = conv_cursor.fetchone()
                        
                        if conv_result and conv_result[0]:
                            actual_user_id = conv_result[0]
                        else:
                            # 如果 Conversation 表沒找到，嘗試 API4Conversation 表
                            api_conv_cursor = DB.execute_sql("""
                                SELECT user_id FROM api_4_conversation WHERE id = %s
                            """, (conversation_id,))
                            api_conv_result = api_conv_cursor.fetchone()
                            
                            if api_conv_result and api_conv_result[0]:
                                actual_user_id = api_conv_result[0]
                        
                        # 如果找到了 user_id，獲取用戶信息
                        if actual_user_id:
                            user = User.get(User.id == actual_user_id)
                            actual_user_email = user.email
                            actual_user_nickname = user.nickname
                            actual_is_superuser = user.is_superuser
                            
                    except Exception as lookup_error:
                        logging.warning(f"Failed to lookup user for conversation_id {conversation_id}: {lookup_error}")
                    
                    # 只有找到實際用戶信息時才添加記錄
                    if actual_user_email and actual_user_id:
                        results.append({
                            'user_id': actual_user_id,
                            'nickname': actual_user_nickname or f'用戶 {actual_user_id}',
                            'user_email': actual_user_email,
                            'is_superuser': actual_is_superuser,
                            'llm_type': llm_type,
                            'llm_name': llm_name,
                            'used_tokens': used_tokens,
                            'token_limit': token_limit,
                            'reset_date': reset_date,
                            'is_active': bool(is_active),
                            'create_date': create_time,
                            'update_date': update_time,
                        })
                    else:
                        logging.warning(f"Skipping conversation_id {conversation_id}: cannot find associated user")
                else:
                    # 跳過沒有 conversation_id 的舊記錄
                    logging.warning(f"Skipping legacy record without conversation_id: user_id={user_id}, llm_name={llm_name}")
            
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
                
                # 活躍用戶數 (有使用過 token 的真實存在用戶)
                # 1. 直接基於 user_id 的記錄
                cursor = DB.execute_sql("""
                    SELECT DISTINCT user_id FROM user_token_usage 
                    WHERE used_tokens > 0 AND user_id IS NOT NULL
                """)
                potential_active_user_ids = set(row[0] for row in cursor.fetchall())
                
                # 2. 基於 conversation_id 的記錄，需要找到對應的實際用戶
                cursor = DB.execute_sql("""
                    SELECT DISTINCT conversation_id FROM user_token_usage 
                    WHERE used_tokens > 0 AND conversation_id IS NOT NULL
                """)
                active_conversation_ids = [row[0] for row in cursor.fetchall()]
                
                # 查找 conversation_id 對應的實際用戶
                conversation_user_ids = set()
                for conv_id in active_conversation_ids:
                    try:
                        # 從 Conversation 表查找
                        conv_cursor = DB.execute_sql("""
                            SELECT user_id FROM conversation WHERE id = %s
                        """, (conv_id,))
                        conv_result = conv_cursor.fetchone()
                        
                        if conv_result and conv_result[0]:
                            conversation_user_ids.add(conv_result[0])
                        else:
                            # 從 API4Conversation 表查找
                            api_conv_cursor = DB.execute_sql("""
                                SELECT user_id FROM api_4_conversation WHERE id = %s
                            """, (conv_id,))
                            api_conv_result = api_conv_cursor.fetchone()
                            
                            if api_conv_result and api_conv_result[0]:
                                conversation_user_ids.add(api_conv_result[0])
                    except Exception as e:
                        logging.warning(f"Failed to lookup user for conversation_id {conv_id} in statistics: {e}")
                
                # 合併所有潛在的活躍用戶ID
                all_potential_active_user_ids = potential_active_user_ids.union(conversation_user_ids)
                
                # 3. 驗證用戶是否真實存在，只統計存在的用戶
                verified_active_user_ids = set()
                for user_id in all_potential_active_user_ids:
                    try:
                        User.get(User.id == user_id, User.status == "1")
                        verified_active_user_ids.add(user_id)
                    except User.DoesNotExist:
                        logging.warning(f"Skipping non-existent user {user_id} in statistics")
                
                active_users = len(verified_active_user_ids)
                
                logging.info(f"Active users: {active_users} (potential: {len(all_potential_active_user_ids)}, verified: {len(verified_active_user_ids)})")
                
                # 記錄一些樣本
                if verified_active_user_ids:
                    logging.info(f"Verified active user samples: {list(verified_active_user_ids)[:3]}")
                if active_conversation_ids:
                    logging.info(f"Active conversation samples: {active_conversation_ids[:3]}")
                
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
                # 1. 直接基於 user_id 的記錄
                cursor = DB.execute_sql("""
                    SELECT DISTINCT user_id FROM user_token_usage 
                    WHERE token_limit > 0 AND used_tokens >= token_limit AND user_id IS NOT NULL
                """)
                potential_over_limit_user_ids = set(row[0] for row in cursor.fetchall())
                
                # 2. 基於 conversation_id 的記錄，需要找到對應的實際用戶
                cursor = DB.execute_sql("""
                    SELECT DISTINCT conversation_id FROM user_token_usage 
                    WHERE token_limit > 0 AND used_tokens >= token_limit AND conversation_id IS NOT NULL
                """)
                over_limit_conversation_ids = [row[0] for row in cursor.fetchall()]
                
                # 查找 conversation_id 對應的實際用戶
                over_limit_conversation_user_ids = set()
                for conv_id in over_limit_conversation_ids:
                    try:
                        # 從 Conversation 表查找
                        conv_cursor = DB.execute_sql("""
                            SELECT user_id FROM conversation WHERE id = %s
                        """, (conv_id,))
                        conv_result = conv_cursor.fetchone()
                        
                        if conv_result and conv_result[0]:
                            over_limit_conversation_user_ids.add(conv_result[0])
                        else:
                            # 從 API4Conversation 表查找
                            api_conv_cursor = DB.execute_sql("""
                                SELECT user_id FROM api_4_conversation WHERE id = %s
                            """, (conv_id,))
                            api_conv_result = api_conv_cursor.fetchone()
                            
                            if api_conv_result and api_conv_result[0]:
                                over_limit_conversation_user_ids.add(api_conv_result[0])
                    except Exception as e:
                        logging.warning(f"Failed to lookup user for over-limit conversation_id {conv_id}: {e}")
                
                # 合併所有潛在的超過限制用戶ID
                all_potential_over_limit_user_ids = potential_over_limit_user_ids.union(over_limit_conversation_user_ids)
                
                # 3. 驗證用戶是否真實存在，只統計存在的用戶
                verified_over_limit_user_ids = set()
                for user_id in all_potential_over_limit_user_ids:
                    try:
                        User.get(User.id == user_id, User.status == "1")
                        verified_over_limit_user_ids.add(user_id)
                    except User.DoesNotExist:
                        logging.warning(f"Skipping non-existent over-limit user {user_id} in statistics")
                
                users_over_limit = len(verified_over_limit_user_ids)
                
                logging.info(f"Users over limit: {users_over_limit} (potential: {len(all_potential_over_limit_user_ids)}, verified: {len(verified_over_limit_user_ids)})")
                
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
        完全依賴 conversation_id 來區分用戶，user_id 只作為必填字段來滿足數據庫約束
        """
        # 強制要求 conversation_id
        if not conversation_id:
            raise ValueError("conversation_id is required for token usage tracking")
        
        try:
            # 只查找基於 conversation_id 的記錄
            record = cls.model.get(
                cls.model.conversation_id == conversation_id,
                cls.model.llm_type == llm_type,
                cls.model.llm_name == llm_name
            )
            return record
        except cls.model.DoesNotExist:
            # 創建新記錄
            # 從 conversation 查找實際 user_id
            actual_user_id = None
            try:
                # 從 Conversation 表查找用戶
                cursor = DB.execute_sql("SELECT user_id FROM conversation WHERE id = %s", (conversation_id,))
                result = cursor.fetchone()
                if result and result[0]:
                    actual_user_id = result[0]
                else:
                    # 從 API4Conversation 表查找用戶
                    cursor = DB.execute_sql("SELECT user_id FROM api_4_conversation WHERE id = %s", (conversation_id,))
                    result = cursor.fetchone()
                    if result and result[0]:
                        actual_user_id = result[0]
            except Exception as e:
                logging.warning(f"Failed to lookup user_id for conversation_id {conversation_id}: {e}")
            
            # 如果還是沒有 user_id，使用 conversation_id 作為替代（滿足數據庫約束）
            if not actual_user_id:
                actual_user_id = conversation_id
                logging.warning(f"Using conversation_id as user_id fallback: {conversation_id}")
            
            # 設置默認限制
            default_limit = settings.NORMAL_USER_TOKEN_LIMIT
            
            # 嘗試檢查是否為管理員（基於實際 user_id）
            if actual_user_id and actual_user_id != conversation_id:
                try:
                    success, user = UserService.get_by_id(actual_user_id)
                    if success and user and user.is_superuser:
                        default_limit = 0
                except Exception:
                    pass
            
            from api.utils import get_uuid
            record_data = {
                "id": get_uuid(),
                "user_id": actual_user_id,
                "conversation_id": conversation_id,  # 主要標識符
                "llm_type": llm_type,
                "llm_name": llm_name,
                "used_tokens": 0,
                "token_limit": default_limit,
                "reset_date": cls._get_next_reset_date(),
                "is_active": True
            }
            
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