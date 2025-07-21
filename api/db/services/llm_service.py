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
from typing import Optional

from langfuse import Langfuse

from api import settings
from api.db import LLMType
from api.db.db_models import DB, LLM, LLMFactories, TenantLLM
from api.db.services.common_service import CommonService
from api.db.services.langfuse_service import TenantLangfuseService
from api.db.services.user_service import TenantService
from api.db.services.user_token_service import UserTokenService
from rag.llm import ChatModel, CvModel, EmbeddingModel, RerankModel, Seq2txtModel, TTSModel


class LLMFactoriesService(CommonService):
    model = LLMFactories


class LLMService(CommonService):
    model = LLM


class TenantLLMService(CommonService):
    model = TenantLLM

    @classmethod
    @DB.connection_context()
    def get_api_key(cls, tenant_id, model_name):
        mdlnm, fid = TenantLLMService.split_model_name_and_factory(model_name)
        if not fid:
            objs = cls.query(tenant_id=tenant_id, llm_name=mdlnm)
        else:
            objs = cls.query(tenant_id=tenant_id, llm_name=mdlnm, llm_factory=fid)
        if not objs:
            return
        return objs[0]

    @classmethod
    @DB.connection_context()
    def get_my_llms(cls, tenant_id):
        fields = [cls.model.llm_factory, LLMFactories.logo, LLMFactories.tags, cls.model.model_type, cls.model.llm_name, cls.model.used_tokens]
        objs = cls.model.select(*fields).join(LLMFactories, on=(cls.model.llm_factory == LLMFactories.name)).where(cls.model.tenant_id == tenant_id, ~cls.model.api_key.is_null()).dicts()

        return list(objs)

    @staticmethod
    def split_model_name_and_factory(model_name):
        arr = model_name.split("@")
        if len(arr) < 2:
            return model_name, None
        if len(arr) > 2:
            return "@".join(arr[0:-1]), arr[-1]

        # model name must be xxx@yyy
        try:
            model_factories = settings.FACTORY_LLM_INFOS
            model_providers = set([f["name"] for f in model_factories])
            if arr[-1] not in model_providers:
                return model_name, None
            return arr[0], arr[-1]
        except Exception as e:
            logging.exception(f"TenantLLMService.split_model_name_and_factory got exception: {e}")
        return model_name, None

    @classmethod
    @DB.connection_context()
    def get_model_config(cls, tenant_id, llm_type, llm_name=None):
        from api.db.services.user_service import UserService
        
        e, tenant = TenantService.get_by_id(tenant_id)
        if not e:
            raise LookupError("Tenant not found")

        if llm_type == LLMType.EMBEDDING.value:
            mdlnm = tenant.embd_id if not llm_name else llm_name
        elif llm_type == LLMType.SPEECH2TEXT.value:
            mdlnm = tenant.asr_id
        elif llm_type == LLMType.IMAGE2TEXT.value:
            mdlnm = tenant.img2txt_id if not llm_name else llm_name
        elif llm_type == LLMType.CHAT.value:
            mdlnm = tenant.llm_id if not llm_name else llm_name
        elif llm_type == LLMType.RERANK:
            mdlnm = tenant.rerank_id if not llm_name else llm_name
        elif llm_type == LLMType.TTS:
            mdlnm = tenant.tts_id if not llm_name else llm_name
        else:
            assert False, "LLM type error"

        # First try to get model config from current tenant
        model_config = cls.get_api_key(tenant_id, mdlnm)
        mdlnm, fid = TenantLLMService.split_model_name_and_factory(mdlnm)
        if model_config:
            model_config = model_config.to_dict()
        
        # If not found, try to inherit from admin users (for non-admin users)
        if not model_config:
            # Check if current user is admin
            success, user = UserService.get_by_id(tenant_id)
            if success and user and not user.is_superuser:
                # Get admin users' LLM configurations
                from api.db.db_models import User
                from api.db import StatusEnum
                admin_users = User.select().where(User.is_superuser == True, User.status == StatusEnum.VALID.value)
                
                for admin_user in admin_users:
                    admin_tenant_id = admin_user.id
                    e_admin, admin_tenant = TenantService.get_by_id(admin_tenant_id)
                    if not e_admin:
                        continue
                    
                    # Get admin's model name for this type
                    admin_mdlnm = None
                    if llm_type == LLMType.EMBEDDING.value:
                        admin_mdlnm = admin_tenant.embd_id
                    elif llm_type == LLMType.SPEECH2TEXT.value:
                        admin_mdlnm = admin_tenant.asr_id
                    elif llm_type == LLMType.IMAGE2TEXT.value:
                        admin_mdlnm = admin_tenant.img2txt_id
                    elif llm_type == LLMType.CHAT.value:
                        admin_mdlnm = admin_tenant.llm_id
                    elif llm_type == LLMType.RERANK:
                        admin_mdlnm = admin_tenant.rerank_id
                    elif llm_type == LLMType.TTS:
                        admin_mdlnm = admin_tenant.tts_id
                    
                    if admin_mdlnm:
                        admin_model_config = cls.get_api_key(admin_tenant_id, admin_mdlnm)
                        if admin_model_config:
                            model_config = admin_model_config.to_dict()
                            break
        
        # If still not found, use default logic
        if not model_config:
            if llm_type in [LLMType.EMBEDDING, LLMType.RERANK]:
                llm = LLMService.query(llm_name=mdlnm) if not fid else LLMService.query(llm_name=mdlnm, fid=fid)
                if llm and llm[0].fid in ["Youdao", "FastEmbed", "BAAI"]:
                    model_config = {"llm_factory": llm[0].fid, "api_key": "", "llm_name": mdlnm, "api_base": ""}
            if not model_config:
                if mdlnm == "flag-embedding":
                    model_config = {"llm_factory": "Tongyi-Qianwen", "api_key": "", "llm_name": llm_name, "api_base": ""}
                else:
                    if not mdlnm:
                        raise LookupError(f"Type of {llm_type} model is not set.")
                    raise LookupError("Model({}) not authorized".format(mdlnm))
        return model_config

    @classmethod
    @DB.connection_context()
    def model_instance(cls, tenant_id, llm_type, llm_name=None, lang="Chinese"):
        model_config = TenantLLMService.get_model_config(tenant_id, llm_type, llm_name)
        if llm_type == LLMType.EMBEDDING.value:
            if model_config["llm_factory"] not in EmbeddingModel:
                return
            return EmbeddingModel[model_config["llm_factory"]](model_config["api_key"], model_config["llm_name"], base_url=model_config["api_base"])

        if llm_type == LLMType.RERANK:
            if model_config["llm_factory"] not in RerankModel:
                return
            return RerankModel[model_config["llm_factory"]](model_config["api_key"], model_config["llm_name"], base_url=model_config["api_base"])

        if llm_type == LLMType.IMAGE2TEXT.value:
            if model_config["llm_factory"] not in CvModel:
                return
            return CvModel[model_config["llm_factory"]](model_config["api_key"], model_config["llm_name"], lang, base_url=model_config["api_base"])

        if llm_type == LLMType.CHAT.value:
            if model_config["llm_factory"] not in ChatModel:
                return
            return ChatModel[model_config["llm_factory"]](model_config["api_key"], model_config["llm_name"], base_url=model_config["api_base"])

        if llm_type == LLMType.SPEECH2TEXT:
            if model_config["llm_factory"] not in Seq2txtModel:
                return
            return Seq2txtModel[model_config["llm_factory"]](key=model_config["api_key"], model_name=model_config["llm_name"], lang=lang, base_url=model_config["api_base"])
        if llm_type == LLMType.TTS:
            if model_config["llm_factory"] not in TTSModel:
                return
            return TTSModel[model_config["llm_factory"]](
                model_config["api_key"],
                model_config["llm_name"],
                base_url=model_config["api_base"],
            )

    @classmethod
    @DB.connection_context()
    def increase_usage(cls, tenant_id, llm_type, used_tokens, llm_name=None):
        e, tenant = TenantService.get_by_id(tenant_id)
        if not e:
            logging.error(f"Tenant not found: {tenant_id}")
            return 0

        llm_map = {
            LLMType.EMBEDDING.value: tenant.embd_id,
            LLMType.SPEECH2TEXT.value: tenant.asr_id,
            LLMType.IMAGE2TEXT.value: tenant.img2txt_id,
            LLMType.CHAT.value: tenant.llm_id if not llm_name else llm_name,
            LLMType.RERANK.value: tenant.rerank_id if not llm_name else llm_name,
            LLMType.TTS.value: tenant.tts_id if not llm_name else llm_name,
        }

        mdlnm = llm_map.get(llm_type)
        if mdlnm is None:
            logging.error(f"LLM type error: {llm_type}")
            return 0

        llm_name, llm_factory = TenantLLMService.split_model_name_and_factory(mdlnm)

        try:
            num = (
                cls.model.update(used_tokens=cls.model.used_tokens + used_tokens)
                .where(cls.model.tenant_id == tenant_id, cls.model.llm_name == llm_name, cls.model.llm_factory == llm_factory if llm_factory else True)
                .execute()
            )
        except Exception:
            logging.exception("TenantLLMService.increase_usage got exception,Failed to update used_tokens for tenant_id=%s, llm_name=%s", tenant_id, llm_name)
            return 0

        return num

    @classmethod
    @DB.connection_context()
    def get_openai_models(cls):
        objs = cls.model.select().where((cls.model.llm_factory == "OpenAI"), ~(cls.model.llm_name == "text-embedding-3-small"), ~(cls.model.llm_name == "text-embedding-3-large")).dicts()
        return list(objs)

    @classmethod
    @DB.connection_context()
    def check_admin_llm_config(cls):
        """检查管理员是否已经配置了LLM"""
        from api.db.db_models import User
        from api.db import StatusEnum
        
        # 获取所有管理员用户
        admin_users = User.select().where(User.is_superuser == True, User.status == StatusEnum.VALID.value)
        
        if not admin_users:
            return False, "No admin users found"
        
        # 检查是否至少有一个管理员配置了Chat LLM
        for admin_user in admin_users:
            admin_tenant_id = admin_user.id
            e_admin, admin_tenant = TenantService.get_by_id(admin_tenant_id)
            if not e_admin:
                continue
            
            # 检查是否有Chat LLM配置
            if admin_tenant.llm_id:
                admin_model_config = cls.get_api_key(admin_tenant_id, admin_tenant.llm_id)
                if admin_model_config:
                    return True, "Admin LLM configured"
        
        return False, "No admin has configured LLM"


class LLMBundle:
    def __init__(self, tenant_id, llm_type, llm_name=None, lang="Chinese", user_id=None, conversation_id: Optional[str] = None):
        self.tenant_id = tenant_id
        self.user_id = user_id or tenant_id  # 如果沒有提供用戶ID，使用tenant_id作為默認值
        self.conversation_id = conversation_id  # 新增：對話會話ID
        self.llm_type = llm_type
        self.llm_name = llm_name
        self.mdl = TenantLLMService.model_instance(tenant_id, llm_type, llm_name, lang=lang)
        assert self.mdl, "Can't find model for {}/{}/{}".format(tenant_id, llm_type, llm_name)
        model_config = TenantLLMService.get_model_config(tenant_id, llm_type, llm_name)
        self.max_length = model_config.get("max_tokens", 8192)

        langfuse_keys = TenantLangfuseService.filter_by_tenant(tenant_id=tenant_id)
        if langfuse_keys:
            langfuse = Langfuse(public_key=langfuse_keys.public_key, secret_key=langfuse_keys.secret_key, host=langfuse_keys.host)
            if langfuse.auth_check():
                self.langfuse = langfuse
                self.trace = self.langfuse.trace(name=f"{self.llm_type}-{self.llm_name}")
        else:
            self.langfuse = None

    def _check_and_record_token_usage(self, tokens_to_use: int, operation_name: str = "") -> bool:
        """
        檢查和記錄用戶 token 使用量
        
        Args:
            tokens_to_use: 即將使用的 token 數量
            operation_name: 操作名稱，用於日誌記錄
            
        Returns:
            bool: 是否允許使用 token
        """
        # 使用實際的 LLM 名稱進行限制檢查
        actual_llm_name = self.llm_name or "default"
        
        # 檢查 token 限制，優先使用 conversation_id
        can_use, error_msg = UserTokenService.check_token_limit(
            user_id=self.user_id,
            llm_type=self.llm_type,
            llm_name=actual_llm_name,
            tokens_to_use=tokens_to_use,
            conversation_id=self.conversation_id
        )
        
        if not can_use:
            identifier = self.conversation_id or self.user_id
            logging.warning(f"Token limit exceeded for identifier {identifier} in {operation_name}: {error_msg}")
            return False
            
        return True

    def _record_token_usage(self, tokens_used: int, operation_name: str = ""):
        """
        記錄用戶 token 使用量
        
        Args:
            tokens_used: 已使用的 token 數量
            operation_name: 操作名稱，用於日誌記錄
        """
        # 使用實際的 LLM 名稱進行使用量記錄
        actual_llm_name = self.llm_name or "default"
        
        # 記錄用戶 token 使用量，優先使用 conversation_id
        success = UserTokenService.increase_token_usage(
            user_id=self.user_id,
            llm_type=self.llm_type,
            llm_name=actual_llm_name,
            tokens_used=tokens_used,
            conversation_id=self.conversation_id
        )
        
        if not success:
            identifier = self.conversation_id or self.user_id
            logging.error(f"Failed to record token usage for identifier {identifier} in {operation_name}: {tokens_used} tokens")
        
        # 同時記錄租戶級別的使用量（保持原有邏輯）
        if not TenantLLMService.increase_usage(self.tenant_id, self.llm_type, tokens_used, self.llm_name):
            logging.error(f"LLMBundle.{operation_name} can't update tenant token usage for {self.tenant_id}/{self.llm_type} used_tokens: {tokens_used}")

    def encode(self, texts: list):
        # 預估 token 使用量
        estimated_tokens = sum(len(text.split()) for text in texts) * 2
        
        # 檢查 token 限制
        if not self._check_and_record_token_usage(estimated_tokens, "encode"):
            return [], 0
        
        if self.langfuse:
            generation = self.trace.generation(name="encode", model=self.llm_name, input={"texts": texts})

        embeddings, used_tokens = self.mdl.encode(texts)
        
        # 記錄實際使用的 token 數量
        if used_tokens > 0:
            self._record_token_usage(used_tokens, "encode")

        if self.langfuse:
            generation.end(usage_details={"total_tokens": used_tokens})

        return embeddings, used_tokens

    def encode_queries(self, query: str):
        if self.langfuse:
            generation = self.trace.generation(name="encode_queries", model=self.llm_name, input={"query": query})

        emd, used_tokens = self.mdl.encode_queries(query)
        if not TenantLLMService.increase_usage(self.tenant_id, self.llm_type, used_tokens):
            logging.error("LLMBundle.encode_queries can't update token usage for {}/EMBEDDING used_tokens: {}".format(self.tenant_id, used_tokens))

        if self.langfuse:
            generation.end(usage_details={"total_tokens": used_tokens})

        return emd, used_tokens

    def similarity(self, query: str, texts: list):
        if self.langfuse:
            generation = self.trace.generation(name="similarity", model=self.llm_name, input={"query": query, "texts": texts})

        sim, used_tokens = self.mdl.similarity(query, texts)
        if not TenantLLMService.increase_usage(self.tenant_id, self.llm_type, used_tokens):
            logging.error("LLMBundle.similarity can't update token usage for {}/RERANK used_tokens: {}".format(self.tenant_id, used_tokens))

        if self.langfuse:
            generation.end(usage_details={"total_tokens": used_tokens})

        return sim, used_tokens

    def describe(self, image, max_tokens=300):
        if self.langfuse:
            generation = self.trace.generation(name="describe", metadata={"model": self.llm_name})

        txt, used_tokens = self.mdl.describe(image)
        if not TenantLLMService.increase_usage(self.tenant_id, self.llm_type, used_tokens):
            logging.error("LLMBundle.describe can't update token usage for {}/IMAGE2TEXT used_tokens: {}".format(self.tenant_id, used_tokens))

        if self.langfuse:
            generation.end(output={"output": txt}, usage_details={"total_tokens": used_tokens})

        return txt

    def describe_with_prompt(self, image, prompt):
        if self.langfuse:
            generation = self.trace.generation(name="describe_with_prompt", metadata={"model": self.llm_name, "prompt": prompt})

        txt, used_tokens = self.mdl.describe_with_prompt(image, prompt)
        if not TenantLLMService.increase_usage(self.tenant_id, self.llm_type, used_tokens):
            logging.error("LLMBundle.describe can't update token usage for {}/IMAGE2TEXT used_tokens: {}".format(self.tenant_id, used_tokens))

        if self.langfuse:
            generation.end(output={"output": txt}, usage_details={"total_tokens": used_tokens})

        return txt

    def transcription(self, audio):
        if self.langfuse:
            generation = self.trace.generation(name="transcription", metadata={"model": self.llm_name})

        txt, used_tokens = self.mdl.transcription(audio)
        if not TenantLLMService.increase_usage(self.tenant_id, self.llm_type, used_tokens):
            logging.error("LLMBundle.transcription can't update token usage for {}/SEQUENCE2TXT used_tokens: {}".format(self.tenant_id, used_tokens))

        if self.langfuse:
            generation.end(output={"output": txt}, usage_details={"total_tokens": used_tokens})

        return txt

    def tts(self, text):
        if self.langfuse:
            span = self.trace.span(name="tts", input={"text": text})

        for chunk in self.mdl.tts(text):
            if isinstance(chunk, int):
                if not TenantLLMService.increase_usage(self.tenant_id, self.llm_type, chunk, self.llm_name):
                    logging.error("LLMBundle.tts can't update token usage for {}/TTS".format(self.tenant_id))
                return
            yield chunk

        if self.langfuse:
            span.end()

    def chat(self, system, history, gen_conf):
        # 預估 token 使用量（基於輸入長度的粗略估算）
        input_text = (system or "") + " ".join([msg.get("content", "") for msg in history])
        estimated_tokens = len(input_text.split()) * 2  # 粗略估算：每個詞約2個token
        
        # 檢查 token 限制
        if not self._check_and_record_token_usage(estimated_tokens, "chat"):
            return "**ERROR**: Token 使用量已達到限制，無法繼續對話。", 0
        
        if self.langfuse:
            generation = self.trace.generation(name="chat", model=self.llm_name, input={"system": system, "history": history})

        txt, used_tokens = self.mdl.chat(system, history, gen_conf)
        
        # 記錄實際使用的 token 數量
        if isinstance(used_tokens, int) and used_tokens > 0:
            self._record_token_usage(used_tokens, "chat")

        if self.langfuse:
            generation.end(output={"output": txt}, usage_details={"total_tokens": used_tokens})

        return txt

    def chat_streamly(self, system, history, gen_conf):
        # 預估 token 使用量（基於輸入長度的粗略估算）
        input_text = (system or "") + " ".join([msg.get("content", "") for msg in history])
        estimated_tokens = len(input_text.split()) * 2  # 粗略估算：每個詞約2個token
        
        # 檢查 token 限制
        if not self._check_and_record_token_usage(estimated_tokens, "chat_streamly"):
            yield "**ERROR**: Token 使用量已達到限制，無法繼續對話。"
            return
        
        if self.langfuse:
            generation = self.trace.generation(name="chat_streamly", model=self.llm_name, input={"system": system, "history": history})

        ans = ""
        for txt in self.mdl.chat_streamly(system, history, gen_conf):
            if isinstance(txt, int):
                if self.langfuse:
                    generation.end(output={"output": ans})

                # 記錄實際使用的 token 數量
                if txt > 0:
                    self._record_token_usage(txt, "chat_streamly")
                return ans

            if txt.endswith("</think>"):
                ans = ans.rstrip("</think>")

            ans += txt
            yield ans
