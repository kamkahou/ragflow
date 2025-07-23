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
import json
import re
from datetime import datetime

from flask import request, session, redirect, Blueprint
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import login_required, current_user, login_user, logout_user

from api.db.db_models import TenantLLM
from api.db.services.llm_service import TenantLLMService, LLMService
from api.db.services.user_token_service import UserTokenService
from api.utils.api_utils import (
    server_error_response,
    validate_request,
    get_data_error_result,
)
from api.utils import (
    get_uuid,
    get_format_time,
    decrypt,
    download_img,
    current_timestamp,
    datetime_format,
)
from api.db import UserTenantRole, FileType
from api import settings
from api.db.services.user_service import UserService, TenantService, UserTenantService
from api.db.services.file_service import FileService
from api.db.services.question_record_service import QuestionRecordService
from api.utils.api_utils import get_json_result, construct_response


manager = Blueprint("user_manager", __name__)


@manager.route("/login", methods=["POST", "GET"])  # noqa: F821
def login():
    """
    User login endpoint.
    ---
    tags:
      - User
    parameters:
      - in: body
        name: body
        description: Login credentials.
        required: true
        schema:
          type: object
          properties:
            email:
              type: string
              description: User email.
            password:
              type: string
              description: User password.
    responses:
      200:
        description: Login successful.
        schema:
          type: object
      401:
        description: Authentication failed.
        schema:
          type: object
    """
    if not request.json:
        return get_json_result(
            data=False, code=settings.RetCode.AUTHENTICATION_ERROR, message="Unauthorized!"
        )

    email = request.json.get("email", "")
    users = UserService.query(email=email)
    if not users:
        return get_json_result(
            data=False,
            code=settings.RetCode.AUTHENTICATION_ERROR,
            message=f"Email: {email} is not registered!",
        )

    password = request.json.get("password")
    try:
        password = decrypt(password)
    except BaseException:
        return get_json_result(
            data=False, code=settings.RetCode.SERVER_ERROR, message="Fail to crypt password"
        )

    user = UserService.query_user(email, password)
    if user:
        response_data = user.to_json()
        user.access_token = get_uuid()
        login_user(user)
        user.update_time = (current_timestamp(),)
        user.update_date = (datetime_format(datetime.now()),)
        user.save()
        msg = "Welcome back!"
        return construct_response(data=response_data, auth=user.get_id(), message=msg)
    else:
        return get_json_result(
            data=False,
            code=settings.RetCode.AUTHENTICATION_ERROR,
            message="Email and password do not match!",
        )


@manager.route("/github_callback", methods=["GET"])  # noqa: F821
def github_callback():
    """
    GitHub OAuth callback endpoint.
    ---
    tags:
      - OAuth
    parameters:
      - in: query
        name: code
        type: string
        required: true
        description: Authorization code from GitHub.
    responses:
      200:
        description: Authentication successful.
        schema:
          type: object
    """
    import requests

    res = requests.post(
        settings.GITHUB_OAUTH.get("url"),
        data={
            "client_id": settings.GITHUB_OAUTH.get("client_id"),
            "client_secret": settings.GITHUB_OAUTH.get("secret_key"),
            "code": request.args.get("code"),
        },
        headers={"Accept": "application/json"},
    )
    res = res.json()
    if "error" in res:
        return redirect("/?error=%s" % res["error_description"])

    if "user:email" not in res["scope"].split(","):
        return redirect("/?error=user:email not in scope")

    session["access_token"] = res["access_token"]
    session["access_token_from"] = "github"
    user_info = user_info_from_github(session["access_token"])
    email_address = user_info["email"]
    users = UserService.query(email=email_address)
    user_id = get_uuid()
    if not users:
        # User isn't try to register
        try:
            try:
                avatar = download_img(user_info["avatar_url"])
            except Exception as e:
                logging.exception(e)
                avatar = ""
            users = user_register(
                user_id,
                {
                    "access_token": session["access_token"],
                    "email": email_address,
                    "avatar": avatar,
                    "nickname": user_info["login"],
                    "login_channel": "github",
                    "last_login_time": get_format_time(),
                    "is_superuser": False,
                },
            )
            if not users:
                raise Exception(f"Fail to register {email_address}.")
            if len(users) > 1:
                raise Exception(f"Same email: {email_address} exists!")

            # Try to log in
            user = users[0]
            login_user(user)
            return redirect("/?auth=%s" % user.get_id())
        except Exception as e:
            rollback_user_registration(user_id)
            logging.exception(e)
            return redirect("/?error=%s" % str(e))

    # User has already registered, try to log in
    user = users[0]
    user.access_token = get_uuid()
    login_user(user)
    user.save()
    return redirect("/?auth=%s" % user.get_id())


@manager.route("/feishu_callback", methods=["GET"])  # noqa: F821
def feishu_callback():
    """
    Feishu OAuth callback endpoint.
    ---
    tags:
      - OAuth
    parameters:
      - in: query
        name: code
        type: string
        required: true
        description: Authorization code from Feishu.
    responses:
      200:
        description: Authentication successful.
        schema:
          type: object
    """
    import requests

    app_access_token_res = requests.post(
        settings.FEISHU_OAUTH.get("app_access_token_url"),
        data=json.dumps(
            {
                "app_id": settings.FEISHU_OAUTH.get("app_id"),
                "app_secret": settings.FEISHU_OAUTH.get("app_secret"),
            }
        ),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    app_access_token_res = app_access_token_res.json()
    if app_access_token_res["code"] != 0:
        return redirect("/?error=%s" % app_access_token_res)

    res = requests.post(
        settings.FEISHU_OAUTH.get("user_access_token_url"),
        data=json.dumps(
            {
                "grant_type": settings.FEISHU_OAUTH.get("grant_type"),
                "code": request.args.get("code"),
            }
        ),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {app_access_token_res['app_access_token']}",
        },
    )
    res = res.json()
    if res["code"] != 0:
        return redirect("/?error=%s" % res["message"])

    if "contact:user.email:readonly" not in res["data"]["scope"].split():
        return redirect("/?error=contact:user.email:readonly not in scope")
    session["access_token"] = res["data"]["access_token"]
    session["access_token_from"] = "feishu"
    user_info = user_info_from_feishu(session["access_token"])
    email_address = user_info["email"]
    users = UserService.query(email=email_address)
    user_id = get_uuid()
    if not users:
        # User isn't try to register
        try:
            try:
                avatar = download_img(user_info["avatar_url"])
            except Exception as e:
                logging.exception(e)
                avatar = ""
            users = user_register(
                user_id,
                {
                    "access_token": session["access_token"],
                    "email": email_address,
                    "avatar": avatar,
                    "nickname": user_info["en_name"],
                    "login_channel": "feishu",
                    "last_login_time": get_format_time(),
                    "is_superuser": False,
                },
            )
            if not users:
                raise Exception(f"Fail to register {email_address}.")
            if len(users) > 1:
                raise Exception(f"Same email: {email_address} exists!")

            # Try to log in
            user = users[0]
            login_user(user)
            return redirect("/?auth=%s" % user.get_id())
        except Exception as e:
            rollback_user_registration(user_id)
            logging.exception(e)
            return redirect("/?error=%s" % str(e))

    # User has already registered, try to log in
    user = users[0]
    user.access_token = get_uuid()
    login_user(user)
    user.save()
    return redirect("/?auth=%s" % user.get_id())


def user_info_from_feishu(access_token):
    import requests

    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Bearer {access_token}",
    }
    res = requests.get(
        "https://open.feishu.cn/open-apis/authen/v1/user_info", headers=headers
    )
    user_info = res.json()["data"]
    user_info["email"] = None if user_info.get("email") == "" else user_info["email"]
    return user_info


def user_info_from_github(access_token):
    import requests

    headers = {"Accept": "application/json", "Authorization": f"token {access_token}"}
    res = requests.get(
        f"https://api.github.com/user?access_token={access_token}", headers=headers
    )
    user_info = res.json()
    email_info = requests.get(
        f"https://api.github.com/user/emails?access_token={access_token}",
        headers=headers,
    ).json()
    user_info["email"] = next(
        (email for email in email_info if email["primary"]), None
    )["email"]
    return user_info


@manager.route("/logout", methods=["GET"])  # noqa: F821
@login_required
def log_out():
    """
    User logout endpoint.
    ---
    tags:
      - User
    security:
      - ApiKeyAuth: []
    responses:
      200:
        description: Logout successful.
        schema:
          type: object
    """
    current_user.access_token = ""
    current_user.save()
    logout_user()
    return get_json_result(data=True)


@manager.route("/setting", methods=["POST"])  # noqa: F821
@login_required
def setting_user():
    """
    Update user settings.
    ---
    tags:
      - User
    security:
      - ApiKeyAuth: []
    parameters:
      - in: body
        name: body
        description: User settings to update.
        required: true
        schema:
          type: object
          properties:
            nickname:
              type: string
              description: New nickname.
            email:
              type: string
              description: New email.
    responses:
      200:
        description: Settings updated successfully.
        schema:
          type: object
    """
    update_dict = {}
    request_data = request.json
    if request_data.get("password"):
        new_password = request_data.get("new_password")
        if not check_password_hash(
                current_user.password, decrypt(request_data["password"])
        ):
            return get_json_result(
                data=False,
                code=settings.RetCode.AUTHENTICATION_ERROR,
                message="Password error!",
            )

        if new_password:
            update_dict["password"] = generate_password_hash(decrypt(new_password))

    for k in request_data.keys():
        if k in [
            "password",
            "new_password",
            "email",
            "status",
            "is_superuser",
            "login_channel",
            "is_anonymous",
            "is_active",
            "is_authenticated",
            "last_login_time",
        ]:
            continue
        update_dict[k] = request_data[k]

    try:
        UserService.update_by_id(current_user.id, update_dict)
        return get_json_result(data=True)
    except Exception as e:
        logging.exception(e)
        return get_json_result(
            data=False, message="Update failure!", code=settings.RetCode.EXCEPTION_ERROR
        )


@manager.route("/info", methods=["GET"])  # noqa: F821
@login_required
def user_profile():
    """
    Get user profile information.
    ---
    tags:
      - User
    security:
      - ApiKeyAuth: []
    responses:
      200:
        description: User profile retrieved successfully.
        schema:
          type: object
          properties:
            id:
              type: string
              description: User ID.
            nickname:
              type: string
              description: User nickname.
            email:
              type: string
              description: User email.
    """
    return get_json_result(data=current_user.to_dict())


def rollback_user_registration(user_id):
    try:
        UserService.delete_by_id(user_id)
    except Exception:
        pass
    try:
        TenantService.delete_by_id(user_id)
    except Exception:
        pass
    try:
        u = UserTenantService.query(tenant_id=user_id)
        if u:
            UserTenantService.delete_by_id(u[0].id)
    except Exception:
        pass
    try:
        TenantLLM.delete().where(TenantLLM.tenant_id == user_id).execute()
    except Exception:
        pass


def user_register(user_id, user):
    user["id"] = user_id
    tenant = {
        "id": user_id,
        "name": user["nickname"] + "‘s Kingdom",
        "llm_id": settings.CHAT_MDL,
        "embd_id": settings.EMBEDDING_MDL,
        "asr_id": settings.ASR_MDL,
        "parser_ids": settings.PARSERS,
        "img2txt_id": settings.IMAGE2TEXT_MDL,
        "rerank_id": settings.RERANK_MDL,
    }
    usr_tenant = {
        "tenant_id": user_id,
        "user_id": user_id,
        "invited_by": user_id,
        "role": UserTenantRole.OWNER,
    }
    file_id = get_uuid()
    file = {
        "id": file_id,
        "parent_id": file_id,
        "tenant_id": user_id,
        "created_by": user_id,
        "name": "/",
        "type": FileType.FOLDER.value,
        "size": 0,
        "location": "",
    }
    tenant_llm = []
    for llm in LLMService.query(fid=settings.LLM_FACTORY):
        tenant_llm.append(
            {
                "tenant_id": user_id,
                "llm_factory": settings.LLM_FACTORY,
                "llm_name": llm.llm_name,
                "model_type": llm.model_type,
                "api_key": settings.API_KEY,
                "api_base": settings.LLM_BASE_URL,
                "max_tokens": llm.max_tokens if llm.max_tokens else 8192
            }
        )

    if not UserService.save(**user):
        return
    TenantService.insert(**tenant)
    UserTenantService.insert(**usr_tenant)
    TenantLLMService.insert_many(tenant_llm)
    FileService.insert(file)
    return UserService.query(email=user["email"])


@manager.route("/register", methods=["POST"])  # noqa: F821
@validate_request("nickname", "email", "password")
def user_add():
    """
    Register a new user.
    ---
    tags:
      - User
    parameters:
      - in: body
        name: body
        description: Registration details.
        required: true
        schema:
          type: object
          properties:
            nickname:
              type: string
              description: User nickname.
            email:
              type: string
              description: User email.
            password:
              type: string
              description: User password.
    responses:
      200:
        description: Registration successful.
        schema:
          type: object
    """

    if not settings.REGISTER_ENABLED:
        return get_json_result(
            data=False,
            message="User registration is disabled!",
            code=settings.RetCode.OPERATING_ERROR,
        )

    req = request.json
    email_address = req["email"]

    # Validate the email address
    if not re.match(r"^[\w\._-]+@([\w_-]+\.)+[\w-]{2,}$", email_address):
        return get_json_result(
            data=False,
            message=f"Invalid email address: {email_address}!",
            code=settings.RetCode.OPERATING_ERROR,
        )

    # Check if the email address is already used
    if UserService.query(email=email_address):
        return get_json_result(
            data=False,
            message=f"Email: {email_address} has already registered!",
            code=settings.RetCode.OPERATING_ERROR,
        )

    # Construct user info data
    nickname = req["nickname"]
    user_dict = {
        "access_token": get_uuid(),
        "email": email_address,
        "nickname": nickname,
        "password": decrypt(req["password"]),
        "login_channel": "password",
        "last_login_time": get_format_time(),
        "is_superuser": False,
    }

    user_id = get_uuid()
    try:
        users = user_register(user_id, user_dict)
        if not users:
            raise Exception(f"Fail to register {email_address}.")
        if len(users) > 1:
            raise Exception(f"Same email: {email_address} exists!")
        user = users[0]
        login_user(user)
        return construct_response(
            data=user.to_json(),
            auth=user.get_id(),
            message=f"{nickname}, welcome aboard!",
        )
    except Exception as e:
        rollback_user_registration(user_id)
        logging.exception(e)
        return get_json_result(
            data=False,
            message=f"User registration failure, error: {str(e)}",
            code=settings.RetCode.EXCEPTION_ERROR,
        )


@manager.route("/tenant_info", methods=["GET"])  # noqa: F821
@login_required
def tenant_info():
    """
    Get tenant information.
    ---
    tags:
      - Tenant
    security:
      - ApiKeyAuth: []
    responses:
      200:
        description: Tenant information retrieved successfully.
        schema:
          type: object
          properties:
            tenant_id:
              type: string
              description: Tenant ID.
            name:
              type: string
              description: Tenant name.
            llm_id:
              type: string
              description: LLM ID.
            embd_id:
              type: string
              description: Embedding model ID.
    """
    try:
        tenants = TenantService.get_info_by(current_user.id)
        if not tenants:
            return get_data_error_result(message="Tenant not found!")
        return get_json_result(data=tenants[0])
    except Exception as e:
        return server_error_response(e)


@manager.route("/set_tenant_info", methods=["POST"])  # noqa: F821
@login_required
@validate_request("tenant_id", "asr_id", "embd_id", "img2txt_id", "llm_id")
def set_tenant_info():
    """
    Update tenant information.
    ---
    tags:
      - Tenant
    security:
      - ApiKeyAuth: []
    parameters:
      - in: body
        name: body
        description: Tenant information to update.
        required: true
        schema:
          type: object
          properties:
            tenant_id:
              type: string
              description: Tenant ID.
            llm_id:
              type: string
              description: LLM ID.
            embd_id:
              type: string
              description: Embedding model ID.
            asr_id:
              type: string
              description: ASR model ID.
            img2txt_id:
              type: string
              description: Image to Text model ID.
    responses:
      200:
        description: Tenant information updated successfully.
        schema:
          type: object
    """
    req = request.json
    try:
        tid = req.pop("tenant_id")
        TenantService.update_by_id(tid, req)
        return get_json_result(data=True)
    except Exception as e:
        return server_error_response(e)


@manager.route("/token_usage", methods=["GET"])  # noqa: F821
@login_required
def get_user_token_usage():
    """
    Get user's token usage information.
    ---
    tags:
      - User Token
    security:
      - ApiKeyAuth: []
    responses:
      200:
        description: User token usage retrieved successfully.
        schema:
          type: object
          properties:
            data:
              type: array
              items:
                type: object
                properties:
                  llm_type:
                    type: string
                    description: LLM type (CHAT, EMBEDDING, etc.)
                  llm_name:
                    type: string
                    description: LLM model name
                  used_tokens:
                    type: integer
                    description: Used tokens count
                  token_limit:
                    type: integer
                    description: Token limit (0 for unlimited)
                  reset_date:
                    type: string
                    description: Next reset date
                  is_active:
                    type: boolean
                    description: Whether limit is active
    """
    try:
        usage_data = UserTokenService.get_user_token_usage(current_user.id)
        return get_json_result(data=usage_data)
    except Exception as e:
        return server_error_response(e)


@manager.route("/token_usage/reset", methods=["POST"])  # noqa: F821
@login_required
def reset_user_token_usage():
    """
    Reset user's token usage (Admin only).
    ---
    tags:
      - User Token
    security:
      - ApiKeyAuth: []
    parameters:
      - in: body
        name: body
        description: Reset parameters.
        schema:
          type: object
          properties:
            user_id:
              type: string
              description: User ID to reset (admin only, optional for self-reset)
            llm_type:
              type: string
              description: LLM type to reset (optional, resets all if not specified)
            llm_name:
              type: string
              description: LLM model name to reset (optional, resets all if not specified)
    responses:
      200:
        description: Token usage reset successfully.
        schema:
          type: object
    """
    try:
        req = request.json or {}
        target_user_id = req.get("user_id")
        llm_type = req.get("llm_type")
        llm_name = req.get("llm_name")
        
        # 如果指定了其他用戶ID，檢查當前用戶是否為管理員
        if target_user_id and target_user_id != current_user.id:
            if not current_user.is_superuser:
                return get_json_result(
                    data=False,
                    message="Only administrators can reset other users' token usage",
                    code=settings.RetCode.PERMISSION_ERROR
                )
        else:
            target_user_id = current_user.id
        
        success = UserTokenService.reset_user_token_usage(target_user_id, llm_type, llm_name)
        if success:
            return get_json_result(data=True, message="Token usage reset successfully")
        else:
            return get_json_result(
                data=False,
                message="Failed to reset token usage",
                code=settings.RetCode.EXCEPTION_ERROR
            )
    except Exception as e:
        return server_error_response(e)


@manager.route("/token_usage/set_limit", methods=["POST"])  # noqa: F821
@login_required
@validate_request("llm_type", "llm_name", "token_limit")
def set_user_token_limit():
    """
    Set user's token limit (Admin only).
    ---
    tags:
      - User Token
    security:
      - ApiKeyAuth: []
    parameters:
      - in: body
        name: body
        description: Token limit settings.
        required: true
        schema:
          type: object
          properties:
            user_id:
              type: string
              description: User ID (admin only, optional for self-setting)
            llm_type:
              type: string
              description: LLM type
            llm_name:
              type: string
              description: LLM model name
            token_limit:
              type: integer
              description: Token limit (0 for unlimited)
    responses:
      200:
        description: Token limit set successfully.
        schema:
          type: object
    """
    try:
        req = request.json
        target_user_id = req.get("user_id")
        llm_type = req["llm_type"]
        llm_name = req["llm_name"]
        token_limit = int(req["token_limit"])
        
        # 如果指定了其他用戶ID，檢查當前用戶是否為管理員
        if target_user_id and target_user_id != current_user.id:
            if not current_user.is_superuser:
                return get_json_result(
                    data=False,
                    message="Only administrators can set other users' token limits",
                    code=settings.RetCode.PERMISSION_ERROR
                )
        else:
            target_user_id = current_user.id
        
        success = UserTokenService.set_user_token_limit(target_user_id, llm_type, llm_name, token_limit)
        if success:
            return get_json_result(data=True, message="Token limit set successfully")
        else:
            return get_json_result(
                data=False,
                message="Failed to set token limit",
                code=settings.RetCode.EXCEPTION_ERROR
            )
    except Exception as e:
        return server_error_response(e)


@manager.route("/admin/token_usage/statistics", methods=["GET"])  # noqa: F821
@login_required
def get_token_usage_statistics():
    """
    Get token usage statistics overview (Admin only).
    ---
    tags:
      - Admin Token Management
    security:
      - ApiKeyAuth: []
    responses:
      200:
        description: Token usage statistics retrieved successfully.
        schema:
          type: object
          properties:
            total_users:
              type: integer
              description: Total number of users
            users_with_limits:
              type: integer
              description: Number of users with token limits
            total_tokens_used:
              type: integer
              description: Total tokens used across all users
            tokens_by_type:
              type: object
              description: Token usage by LLM type
            statistics_date:
              type: string
              description: Statistics generation date
    """
    try:
        if not current_user.is_superuser:
            return get_json_result(
                data=False,
                message="Only administrators can access token usage statistics",
                code=settings.RetCode.PERMISSION_ERROR
            )
        
        statistics = UserTokenService.get_token_usage_statistics()
        return get_json_result(data=statistics)
    except Exception as e:
        return server_error_response(e)


@manager.route("/admin/token_usage/users", methods=["GET"])  # noqa: F821
@login_required
def get_all_users_token_usage():
    """Get all users' token usage for admin."""
    logging.info(f"Admin user {current_user.email} is requesting all users' token usage.")
    if not current_user.is_superuser:
        logging.warning(f"Permission denied for user {current_user.email} to access all users' token usage.")
        return get_data_error_result(
            message="Only super admin is authorized for this operation."
        )

    try:
        limit = int(request.args.get("limit", 100))
        offset = int(request.args.get("offset", 0))
        logging.info(f"Fetching token usage with limit={limit}, offset={offset}")

        usage_data = UserTokenService.get_all_users_token_usage(limit, offset)
        logging.info(f"Successfully fetched {len(usage_data)} records for token usage.")
        return get_json_result(data=usage_data)
    except Exception as e:
        logging.error(f"Error getting all users token usage: {e}", exc_info=True)
        return server_error_response(e)


# ======================== Question Record Management ========================

@manager.route('/question_record/list', methods=['GET'])
@login_required
def get_question_list():
    """Gets the list of question records."""
    if not (hasattr(current_user, 'is_superuser') and current_user.is_superuser):
        return get_data_error_result(message="Permission denied. Admin access required.")

    try:
        page = int(request.args.get('page', 1))
        page_size = int(request.args.get('page_size', 20))
        questions, total = QuestionRecordService.get_paged_list(page, page_size, **request.args.to_dict())
        return get_json_result(data={'list': questions, 'total': total, 'page': page, 'page_size': page_size})

    except Exception as e:
        return server_error_response(e)

@manager.route('/question_record/stats', methods=['GET'])
@login_required
def get_question_stats():
    """Gets question statistics."""
    if not (hasattr(current_user, 'is_superuser') and current_user.is_superuser):
        return get_data_error_result(message="Permission denied. Admin access required.")

    try:
        stats = QuestionRecordService.get_stats(**request.args.to_dict())
        return get_json_result(data=stats)

    except Exception as e:
        return server_error_response(e)

@manager.route('/question_record/export', methods=['GET'])
@login_required
def export_questions():
    """Exports question records."""
    if not (hasattr(current_user, 'is_superuser') and current_user.is_superuser):
        return get_data_error_result(message="Permission denied. Admin access required.")

    try:
        questions = QuestionRecordService.get_all(**request.args.to_dict())
        return get_json_result(data=questions)

    except Exception as e:
        return server_error_response(e)

@manager.route('/question_record/delete', methods=['POST'])
@login_required
@validate_request("question_ids")
def delete_questions():
    """Deletes question records."""
    if not (hasattr(current_user, 'is_superuser') and current_user.is_superuser):
        return get_data_error_result(message="Permission denied. Admin access required.")

    try:
        deleted_count = QuestionRecordService.batch_delete(request.json['question_ids'])
        return get_json_result(data={'deleted_count': deleted_count})

    except Exception as e:
        return server_error_response(e)


# 設置自定義的 URL 前綴
page_name = "user"
