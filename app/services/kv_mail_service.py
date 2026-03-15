"""
Cloudflare Workers KV 郵件服務

從 Cloudflare Workers KV 讀取由 Email Worker 存儲的郵件。
"""

import json
import time
import traceback
from datetime import datetime
from typing import List, Optional, Dict, Any
from urllib.parse import quote
import httpx

from app.config import settings
from app.models import Mail
from app.services.log_service import log_service, LogLevel, LogType
from app.services.cache_service import mail_index_cache, mail_content_cache


class CloudflareKVClient:
    """Cloudflare Workers KV 客戶端"""

    def __init__(self):
        self._account_id = settings.cf_account_id
        self._namespace_id = settings.cf_kv_namespace_id
        self._api_token = settings.cf_api_token
        self._headers = {}
        self._base_url = ""

        # 初始化 URL 和 headers
        self._update_base_url()
        self._update_headers()

        # 驗證配置
        self._validate_config()

    @property
    def account_id(self):
        """獲取 Account ID"""
        return self._account_id

    @account_id.setter
    def account_id(self, value):
        """設置 Account ID 並自動更新 base_url"""
        self._account_id = value
        self._update_base_url()
        self._validate_config()

    @property
    def namespace_id(self):
        """獲取 Namespace ID"""
        return self._namespace_id

    @namespace_id.setter
    def namespace_id(self, value):
        """設置 Namespace ID 並自動更新 base_url"""
        self._namespace_id = value
        self._update_base_url()
        self._validate_config()

    @property
    def api_token(self):
        """獲取 API Token"""
        return self._api_token

    @api_token.setter
    def api_token(self, value):
        """設置 API Token 並自動更新 headers"""
        self._api_token = value
        self._update_headers()
        self._validate_config()

    @property
    def base_url(self):
        """獲取 Base URL"""
        return self._base_url

    @property
    def headers(self):
        """獲取 Headers"""
        return self._headers

    def _update_base_url(self):
        """更新 Base URL"""
        if self._account_id and self._namespace_id:
            self._base_url = f"https://api.cloudflare.com/client/v4/accounts/{self._account_id}/storage/kv/namespaces/{self._namespace_id}"
        else:
            self._base_url = ""

    def _update_headers(self):
        """更新請求頭"""
        if self._api_token and self._api_token.strip():
            self._headers = {
                "Authorization": f"Bearer {self._api_token}",
                "Content-Type": "application/json",
            }
        else:
            self._headers = {
                "Content-Type": "application/json",
            }

    def _validate_config(self):
        """驗證配置完整性"""
        import asyncio

        errors = []
        if not self._account_id or not self._account_id.strip():
            errors.append("CF_ACCOUNT_ID is empty")
        if not self._namespace_id or not self._namespace_id.strip():
            errors.append("CF_NAMESPACE_ID is empty")
        if not self._api_token or not self._api_token.strip():
            errors.append("CF_API_TOKEN is empty")

        if errors:
            # 異步記錄錯誤（不阻塞初始化）
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(log_service.log(
                        level=LogLevel.ERROR,
                        log_type=LogType.KV_ACCESS,
                        message=f"Cloudflare KV configuration incomplete: {', '.join(errors)}",
                        details={"errors": errors}
                    ))
            except Exception:
                # 如果沒有事件循環，使用同步日誌
                import logging
                logging.error(f"Cloudflare KV configuration incomplete: {', '.join(errors)}")

    async def fetch_mails(self, email: str, fetch_full_content: bool = False) -> List[Mail]:
        """
        從 KV 獲取指定郵箱的所有郵件

        Args:
            email: 郵箱地址
            fetch_full_content: 是否獲取完整郵件內容（默認 False，只從索引獲取摘要）

        Returns:
            郵件列表
        """
        start_time = time.time()

        try:
            await log_service.log(
                level=LogLevel.INFO,
                log_type=LogType.KV_ACCESS,
                message=f"Fetching mails for {email} (full_content={fetch_full_content})",
                details={"email": email, "operation": "fetch_mails", "fetch_full_content": fetch_full_content}
            )

            # 首先嘗試從緩存獲取郵件索引
            index_key = f"index:{email}"
            index_data = mail_index_cache.get(index_key)

            if not index_data:
                # 緩存未命中，從 KV 讀取
                index_data = await self._get_kv_value(index_key)
                if index_data:
                    # 存入緩存 (TTL: 30 秒)
                    mail_index_cache.set(index_key, index_data, ttl=30)

            if index_data:
                # 從索引獲取郵件列表
                mail_list = index_data.get("mails", [])
                mails = []

                if fetch_full_content:
                    # 批量獲取完整郵件內容（僅在需要時）
                    for mail_info in mail_list:
                        mail_key = mail_info.get("key")
                        if mail_key:
                            # 先嘗試從緩存獲取
                            mail_data = mail_content_cache.get(mail_key)
                            if not mail_data:
                                # 緩存未命中，從 KV 讀取
                                mail_data = await self._get_kv_value(mail_key)
                                if mail_data:
                                    # 存入緩存 (TTL: 5 分鐘)
                                    mail_content_cache.set(mail_key, mail_data, ttl=300)

                            if mail_data:
                                mail = self._parse_mail_data(mail_data)
                                if mail:
                                    mails.append(mail)
                else:
                    # 直接從索引構建 Mail 對象（優化：減少 KV 讀取）
                    for mail_info in mail_list:
                        mail = self._parse_mail_from_index(mail_info)
                        if mail:
                            mails.append(mail)

                duration_ms = (time.time() - start_time) * 1000
                await log_service.log(
                    level=LogLevel.SUCCESS,
                    log_type=LogType.KV_ACCESS,
                    message=f"Successfully fetched {len(mails)} mails from index (KV reads: {'N+1' if fetch_full_content else '1'})",
                    details={"email": email, "count": len(mails), "method": "index", "kv_reads_optimized": not fetch_full_content},
                    duration_ms=duration_ms
                )

                return mails
            else:
                # 如果沒有索引，使用 prefix 搜索
                return await self._fetch_mails_by_prefix(email)

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            await log_service.log(
                level=LogLevel.ERROR,
                log_type=LogType.KV_ACCESS,
                message=f"Failed to fetch mails: {str(e)}",
                details={
                    "email": email,
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "traceback": traceback.format_exc()
                },
                duration_ms=duration_ms
            )
            return []

    async def _fetch_mails_by_prefix(self, email: str) -> List[Mail]:
        """
        通過 prefix 搜索獲取郵件

        Args:
            email: 郵箱地址

        Returns:
            郵件列表
        """
        start_time = time.time()

        try:
            # 列出所有匹配 prefix 的 key
            prefix = f"mail:{email}:"
            keys = await self._list_keys(prefix)

            await log_service.log(
                level=LogLevel.INFO,
                log_type=LogType.KV_ACCESS,
                message=f"Fetching mails by prefix: {prefix}",
                details={"email": email, "prefix": prefix, "keys_found": len(keys)}
            )

            mails = []
            for key_name in keys:
                mail_data = await self._get_kv_value(key_name)
                if mail_data:
                    mail = self._parse_mail_data(mail_data)
                    if mail:
                        mails.append(mail)

            # 按接收時間排序
            mails.sort(key=lambda m: m.received_at, reverse=True)

            duration_ms = (time.time() - start_time) * 1000
            await log_service.log(
                level=LogLevel.SUCCESS,
                log_type=LogType.KV_ACCESS,
                message=f"Successfully fetched {len(mails)} mails by prefix",
                details={"email": email, "count": len(mails), "method": "prefix"},
                duration_ms=duration_ms
            )

            return mails

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            await log_service.log(
                level=LogLevel.ERROR,
                log_type=LogType.KV_ACCESS,
                message=f"Failed to fetch mails by prefix: {str(e)}",
                details={
                    "email": email,
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "traceback": traceback.format_exc()
                },
                duration_ms=duration_ms
            )
            return []

    async def _get_kv_value(self, key: str) -> Optional[Dict[str, Any]]:
        """
        從 KV 獲取單個值

        Args:
            key: KV 鍵名

        Returns:
            值（JSON 對象）或 None
        """
        try:
            url = self._build_value_url(key)

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers=self.headers)

                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 404:
                    return None
                else:
                    await log_service.log(
                        level=LogLevel.WARNING,
                        log_type=LogType.KV_ACCESS,
                        message=f"KV GET returned non-200 status: {response.status_code}",
                        details={"key": key, "status_code": response.status_code}
                    )
                    return None

        except Exception as e:
            await log_service.log(
                level=LogLevel.ERROR,
                log_type=LogType.KV_ACCESS,
                message=f"Failed to get KV key: {str(e)}",
                details={
                    "key": key,
                    "error_type": type(e).__name__,
                    "error_message": str(e)
                }
            )
            return None

    def _build_value_url(self, key: str) -> str:
        """構建單個 KV value API URL，避免特殊字符導致路徑解析問題。"""
        return f"{self.base_url}/values/{quote(key, safe='')}"

    async def _delete_kv_key(self, key: str) -> Dict[str, Any]:
        """
        刪除單個 KV key。

        Returns:
            包含 status/success/status_code 的結果字典
        """
        try:
            url = self._build_value_url(key)

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.delete(url, headers=self.headers)

            if response.status_code in (200, 204):
                return {"success": True, "status": "deleted", "status_code": response.status_code}

            if response.status_code == 404:
                return {"success": True, "status": "not_found", "status_code": response.status_code}

            return {
                "success": False,
                "status": "error",
                "status_code": response.status_code,
                "response": response.text[:500],
            }

        except Exception as e:
            return {
                "success": False,
                "status": "error",
                "error_type": type(e).__name__,
                "error_message": str(e),
            }

    async def delete_email_data(self, email: str) -> Dict[str, Any]:
        """
        刪除指定郵箱在 Cloudflare KV 中的索引與郵件正文。

        Args:
            email: 郵箱地址

        Returns:
            刪除結果統計
        """
        start_time = time.time()
        index_key = f"index:{email}"
        used_prefix_fallback = False
        mail_keys: List[str] = []
        deleted_keys: List[str] = []
        missing_keys: List[str] = []
        errors: List[Dict[str, Any]] = []

        try:
            await log_service.log(
                level=LogLevel.INFO,
                log_type=LogType.KV_ACCESS,
                message=f"Deleting mailbox data from KV: {email}",
                details={"email": email, "operation": "delete_email_data"},
            )

            index_data = mail_index_cache.get(index_key)
            if not index_data:
                index_data = await self._get_kv_value(index_key)

            if index_data:
                for mail_info in index_data.get("mails", []):
                    mail_key = mail_info.get("key")
                    if mail_key and mail_key not in mail_keys:
                        mail_keys.append(mail_key)

            if not mail_keys:
                used_prefix_fallback = True
                # 每個郵箱最多保留 50 封，額外留出餘量以兼容舊數據。
                prefix_limit = max(getattr(settings, "max_mails_per_email", 50) * 2, 100)
                mail_keys = await self._list_keys(f"mail:{email}:", limit=prefix_limit)

            keys_to_delete = [index_key, *mail_keys]

            for key in keys_to_delete:
                delete_result = await self._delete_kv_key(key)
                if delete_result["success"]:
                    if delete_result["status"] == "deleted":
                        deleted_keys.append(key)
                    else:
                        missing_keys.append(key)
                else:
                    errors.append({"key": key, **delete_result})

            mail_index_cache.delete(index_key)
            for mail_key in mail_keys:
                mail_content_cache.delete(mail_key)

            success = len(errors) == 0
            duration_ms = (time.time() - start_time) * 1000
            log_level = LogLevel.SUCCESS if success else LogLevel.ERROR

            await log_service.log(
                level=log_level,
                log_type=LogType.KV_ACCESS,
                message=(
                    f"KV mailbox deletion {'completed' if success else 'partially failed'}: {email}"
                ),
                details={
                    "email": email,
                    "deleted_keys": len(deleted_keys),
                    "missing_keys": len(missing_keys),
                    "mail_keys_detected": len(mail_keys),
                    "used_prefix_fallback": used_prefix_fallback,
                    "errors": errors,
                },
                duration_ms=duration_ms,
            )

            return {
                "success": success,
                "email": email,
                "deletedKeys": deleted_keys,
                "deletedCount": len(deleted_keys),
                "deletedMailCount": len([key for key in deleted_keys if key.startswith("mail:")]),
                "deletedIndex": index_key in deleted_keys,
                "missingKeys": missing_keys,
                "missingCount": len(missing_keys),
                "usedPrefixFallback": used_prefix_fallback,
                "errors": errors,
            }

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            error_result = {
                "success": False,
                "email": email,
                "deletedKeys": deleted_keys,
                "deletedCount": len(deleted_keys),
                "deletedMailCount": len([key for key in deleted_keys if key.startswith("mail:")]),
                "deletedIndex": index_key in deleted_keys,
                "missingKeys": missing_keys,
                "missingCount": len(missing_keys),
                "usedPrefixFallback": used_prefix_fallback,
                "errors": [
                    *errors,
                    {
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                        "traceback": traceback.format_exc(),
                    },
                ],
            }

            await log_service.log(
                level=LogLevel.ERROR,
                log_type=LogType.KV_ACCESS,
                message=f"Failed to delete mailbox data from KV: {str(e)}",
                details={
                    "email": email,
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "traceback": traceback.format_exc(),
                },
                duration_ms=duration_ms,
            )

            return error_result

    async def _list_keys(self, prefix: str, limit: int = 20) -> List[str]:
        """
        列出匹配 prefix 的所有 key

        Args:
            prefix: key 前綴
            limit: 最大返回數量 (優化：從 100 降低到 20)

        Returns:
            key 列表
        """
        try:
            url = f"{self.base_url}/keys"
            params = {"prefix": prefix, "limit": limit}

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers=self.headers, params=params)

                if response.status_code == 200:
                    data = response.json()
                    if data.get("success"):
                        keys = data.get("result", [])
                        return [k["name"] for k in keys]

            return []

        except Exception as e:
            await log_service.log(
                level=LogLevel.ERROR,
                log_type=LogType.KV_ACCESS,
                message=f"Failed to list KV keys: {str(e)}",
                details={
                    "prefix": prefix,
                    "limit": limit,
                    "error_type": type(e).__name__,
                    "error_message": str(e)
                }
            )
            return []

    def _parse_mail_data(self, data: Dict[str, Any]) -> Optional[Mail]:
        """
        將 KV 數據解析為 Mail 對象

        Args:
            data: KV 中存儲的郵件數據

        Returns:
            Mail 對象或 None
        """
        try:
            # 解析接收時間
            received_at_str = data.get("received_at")
            if received_at_str:
                try:
                    received_at = datetime.fromisoformat(received_at_str.replace("Z", "+00:00"))
                except ValueError:
                    received_at = datetime.now()
            else:
                received_at = datetime.now()

            # 構建 Mail 對象
            mail = Mail(
                id=data.get("id", "unknown"),
                email_token="",  # 將在存儲時設置
                **{
                    "from": data.get("from", "unknown"),
                    "to": data.get("to", ""),
                    "subject": data.get("subject", "(No Subject)"),
                    "content": data.get("content", ""),
                    "html_content": data.get("html_content"),
                    "received_at": received_at,
                    "read": False,
                },
            )

            return mail

        except Exception as e:
            import asyncio
            asyncio.create_task(log_service.log(
                level=LogLevel.ERROR,
                log_type=LogType.KV_ACCESS,
                message=f"Failed to parse mail data: {str(e)}",
                details={
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "data_keys": list(data.keys()) if isinstance(data, dict) else None
                }
            ))
            return None

    def _parse_mail_from_index(self, mail_info: Dict[str, Any]) -> Optional[Mail]:
        """
        從索引數據構建 Mail 對象（優化版本，無需讀取完整郵件內容）

        Args:
            mail_info: 索引中的郵件摘要信息 (包含 id, from, subject, receivedAt)

        Returns:
            Mail 對象或 None
        """
        try:
            # 解析接收時間
            received_at_str = mail_info.get("receivedAt")
            if received_at_str:
                try:
                    received_at = datetime.fromisoformat(received_at_str.replace("Z", "+00:00"))
                except ValueError:
                    received_at = datetime.now()
            else:
                received_at = datetime.now()

            # 從索引構建簡化的 Mail 對象
            # 注意：content 使用 content_preview（從索引獲取），如需完整內容需再讀取
            content_preview = mail_info.get("content_preview", "")

            mail = Mail(
                id=mail_info.get("id", "unknown"),
                email_token="",  # 將在存儲時設置
                **{
                    "from": mail_info.get("from", "unknown"),
                    "to": mail_info.get("email", ""),  # 索引中沒有 to 字段，使用 email 字段
                    "subject": mail_info.get("subject", "(No Subject)"),
                    "content": content_preview,  # 使用索引中的摘要
                    "html_content": None,
                    "received_at": received_at,
                    "read": False,
                },
            )

            return mail

        except Exception as e:
            import asyncio
            asyncio.create_task(log_service.log(
                level=LogLevel.ERROR,
                log_type=LogType.KV_ACCESS,
                message=f"Failed to parse mail from index: {str(e)}",
                details={
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "mail_info_keys": list(mail_info.keys()) if isinstance(mail_info, dict) else None
                }
            ))
            return None

    async def test_connection(self) -> bool:
        """
        測試 KV 連接

        Returns:
            是否連接成功
        """
        try:
            url = f"{self.base_url}/keys"
            params = {"limit": 1}

            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(url, headers=self.headers, params=params)
                success = response.status_code == 200

                await log_service.log(
                    level=LogLevel.SUCCESS if success else LogLevel.ERROR,
                    log_type=LogType.KV_ACCESS,
                    message=f"KV connection test: {'success' if success else 'failed'}",
                    details={"status_code": response.status_code}
                )

                return success

        except Exception as e:
            await log_service.log(
                level=LogLevel.ERROR,
                log_type=LogType.KV_ACCESS,
                message=f"KV connection test failed: {str(e)}",
                details={
                    "error_type": type(e).__name__,
                    "error_message": str(e)
                }
            )
            return False

    async def get_stats(self) -> Dict[str, Any]:
        """
        獲取 KV 統計信息

        Returns:
            統計信息字典
        """
        try:
            # 獲取所有 key（用於統計）
            url = f"{self.base_url}/keys"
            params = {"limit": 1000}

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers=self.headers, params=params)

                if response.status_code == 200:
                    data = response.json()
                    if data.get("success"):
                        keys = data.get("result", [])

                        # 統計不同類型的 key
                        mail_keys = [k for k in keys if k["name"].startswith("mail:")]
                        index_keys = [k for k in keys if k["name"].startswith("index:")]

                        return {
                            "total_keys": len(keys),
                            "mail_keys": len(mail_keys),
                            "index_keys": len(index_keys),
                            "connected": True,
                        }

            return {"connected": False}

        except Exception as e:
            await log_service.log(
                level=LogLevel.ERROR,
                log_type=LogType.KV_ACCESS,
                message=f"Failed to get KV stats: {str(e)}",
                details={
                    "error_type": type(e).__name__,
                    "error_message": str(e)
                }
            )
            return {"connected": False, "error": str(e)}


# 單例
kv_client = CloudflareKVClient()
