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
    def check_token_limit(cls, user_id: str, llm_type: str, llm_name: str, tokens_to_use: int) -> tuple[bool, str]:
        """
        檢查用戶是否可以使用指定數量的 token
        
        Args:
            user_id: 用戶 ID
            llm_type: LLM 類型 (CHAT, EMBEDDING, etc.)
            llm_name: LLM 模型名稱
            tokens_to_use: 即將使用的 token 數量
            
        Returns:
            tuple[bool, str]: (是否允許使用, 錯誤消息)
        """
        if not settings.TOKEN_LIMIT_ENABLED:
            return True, ""
            
        # 檢查用戶是否為管理員
        success, user = UserService.get_by_id(user_id)
        if success and user and user.is_superuser:
            return True, ""
            
        # 獲取或創建用戶 token 使用記錄
        usage_record = cls._get_or_create_usage_record(user_id, llm_type, llm_name)
        
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
    def increase_token_usage(cls, user_id: str, llm_type: str, llm_name: str, tokens_used: int) -> bool:
        """
        增加用戶的 token 使用量
        
        Args:
            user_id: 用戶 ID
            llm_type: LLM 類型
            llm_name: LLM 模型名稱
            tokens_used: 使用的 token 數量
            
        Returns:
            bool: 是否成功更新
        """
        try:
            # 獲取或創建用戶 token 使用記錄
            usage_record = cls._get_or_create_usage_record(user_id, llm_type, llm_name)
            
            # 檢查是否需要重置使用量
            if cls._should_reset_usage(usage_record):
                cls._reset_usage(usage_record)
                
            # 更新使用量
            num = (
                cls.model.update(used_tokens=cls.model.used_tokens + tokens_used)
                .where(
                    cls.model.user_id == user_id,
                    cls.model.llm_type == llm_type,
                    cls.model.llm_name == llm_name
                )
                .execute()
            )
            
            return num > 0
            
        except Exception as e:
            logging.error(f"Failed to increase token usage for user {user_id}: {e}")
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
        
        Args:
            limit: 限制返回數量
            offset: 偏移量
            
        Returns:
            List[Dict]: 用戶 token 使用統計列表
        """
        try:
            # 首先獲取所有用戶
            all_users = User.select().where(User.status == "1").order_by(User.id)
            
            results = []
            for user in all_users:
                # 獲取該用戶的所有token使用記錄
                user_token_records = cls.model.select().where(cls.model.user_id == user.id)
                
                if user_token_records.exists():
                    # 如果用戶有token使用記錄，添加所有記錄
                    for record in user_token_records:
                        results.append({
                            'user_id': user.id,
                            'nickname': user.nickname,
                            'user_email': user.email,  # 修改為 user_email 以匹配前端
                            'is_superuser': user.is_superuser,
                            'llm_type': record.llm_type,
                            'llm_name': record.llm_name,
                            'used_tokens': record.used_tokens,
                            'token_limit': record.token_limit,
                            'reset_date': record.reset_date,
                            'is_active': record.is_active,
                            'create_date': record.create_date,
                            'update_date': record.update_date,
                        })
                else:
                    # 如果用戶沒有token使用記錄，創建一個預設記錄顯示
                    default_limit = 0 if user.is_superuser else getattr(settings, 'NORMAL_USER_TOKEN_LIMIT', 0)
                    results.append({
                        'user_id': user.id,
                        'nickname': user.nickname,
                        'user_email': user.email,  # 修改為 user_email 以匹配前端
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
            results.sort(key=lambda x: x['update_date'], reverse=True)
            start_idx = offset
            end_idx = offset + limit
            
            return results[start_idx:end_idx]
            
        except Exception as e:
            logging.error(f"Failed to get all users token usage: {e}", exc_info=True)
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
                cls.model.select().limit(1).execute()
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
            
            # 檢查是否有任何 token 使用記錄
            total_records = cls.model.select().count()
            logging.info(f"Total token usage records: {total_records}")
            
            # 活躍用戶數 (有使用過 token 的用戶)
            active_users_query = cls.model.select(cls.model.user_id).where(cls.model.used_tokens > 0).distinct()
            active_users = active_users_query.count()
            logging.info(f"Active users query executed, count: {active_users}")
            
            # 詳細查看活躍用戶
            if active_users > 0:
                for user_record in active_users_query.limit(5):
                    logging.info(f"Active user sample: {user_record.user_id}")
            
            # 總 token 使用量
            total_tokens_used_query = cls.model.select(fn.Sum(cls.model.used_tokens)).scalar()
            total_tokens_used = total_tokens_used_query or 0
            logging.info(f"Total tokens used: {total_tokens_used}")
            
            # 總 token 限制量 (所有用戶的 token 限制總和，0 表示無限制的不計算在內)
            total_tokens_limit_query = cls.model.select(fn.Sum(cls.model.token_limit)).where(cls.model.token_limit > 0).scalar()
            total_tokens_limit = total_tokens_limit_query or 0
            logging.info(f"Total tokens limit: {total_tokens_limit}")
            
            # 超過限制的用戶數
            users_over_limit_query = cls.model.select(cls.model.user_id).where(
                (cls.model.token_limit > 0) & (cls.model.used_tokens >= cls.model.token_limit)
            ).distinct()
            users_over_limit = users_over_limit_query.count()
            logging.info(f"Users over limit: {users_over_limit}")
            
            # 按類型統計 token 使用量
            type_stats = {}
            type_query = cls.model.select(
                cls.model.llm_type,
                fn.Sum(cls.model.used_tokens).alias('total_tokens')
            ).group_by(cls.model.llm_type)
            
            for record in type_query.dicts():
                type_stats[record['llm_type']] = record['total_tokens']
            logging.info(f"Type stats: {type_stats}")
            
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
    def _get_or_create_usage_record(cls, user_id: str, llm_type: str, llm_name: str) -> UserTokenUsage:
        """
        獲取或創建用戶 token 使用記錄
        """
        try:
            record = cls.model.get(
                cls.model.user_id == user_id,
                cls.model.llm_type == llm_type,
                cls.model.llm_name == llm_name
            )
            return record
        except cls.model.DoesNotExist:
            # 創建新記錄
            success, user = UserService.get_by_id(user_id)
            default_limit = 0 if (success and user and user.is_superuser) else settings.NORMAL_USER_TOKEN_LIMIT
            
            record = cls.model.create(
                user_id=user_id,
                llm_type=llm_type,
                llm_name=llm_name,
                used_tokens=0,
                token_limit=default_limit,
                reset_date=cls._get_next_reset_date(),
                is_active=True
            )
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