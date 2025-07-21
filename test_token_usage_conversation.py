#!/usr/bin/env python3
"""
測試基於 conversation_id 的 token 計量功能
"""

import sys
import os
sys.path.append('.')

from api.db.services.user_token_service import UserTokenService
from api import settings

def test_conversation_based_token_usage():
    """測試基於 conversation_id 的 token 使用計量"""
    print("測試基於 conversation_id 的 token 計量功能...")
    
    # 測試參數
    conversation_id = "test_conversation_123"
    llm_type = "CHAT"
    llm_name = "test_model"
    tokens_to_use = 100
    
    try:
        # 初始化設置
        settings.init_settings()
        
        # 測試 1: 檢查 token 限制（首次使用）
        print(f"1. 檢查 conversation {conversation_id} 的 token 限制...")
        can_use, error_msg = UserTokenService.check_token_limit(
            conversation_id=conversation_id,
            llm_type=llm_type,
            llm_name=llm_name,
            tokens_to_use=tokens_to_use
        )
        print(f"   結果: {'允許使用' if can_use else '禁止使用'}")
        if error_msg:
            print(f"   錯誤信息: {error_msg}")
        
        # 測試 2: 記錄 token 使用量
        print(f"2. 記錄 conversation {conversation_id} 使用了 {tokens_to_use} tokens...")
        success = UserTokenService.increase_token_usage(
            conversation_id=conversation_id,
            llm_type=llm_type,
            llm_name=llm_name,
            tokens_used=tokens_to_use
        )
        print(f"   結果: {'成功' if success else '失敗'}")
        
        # 測試 3: 再次檢查 token 限制
        print(f"3. 再次檢查 conversation {conversation_id} 的 token 限制...")
        can_use, error_msg = UserTokenService.check_token_limit(
            conversation_id=conversation_id,
            llm_type=llm_type,
            llm_name=llm_name,
            tokens_to_use=tokens_to_use
        )
        print(f"   結果: {'允許使用' if can_use else '禁止使用'}")
        if error_msg:
            print(f"   錯誤信息: {error_msg}")
            
        print("\n✅ 基於 conversation_id 的 token 計量功能測試完成！")
        return True
        
    except Exception as e:
        print(f"❌ 測試失敗: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    test_conversation_based_token_usage() 