"""HTTP 出口：`requests` 的**唯一**使用者（架构文档 §4）。

约定：
    * 所有方法返回 `ApiResult`，**不抛异常**（网络错误 → `status_code=0`），
      便于离线队列按状态码分类处理；
    * 需要异常语义时用 `raise_for_result(result)`；
    * 一律携带 `X-Client-Id`，有 token 时再带 `X-Client-Token`。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping, Optional

import requests

from .constants import DEFAULT_HTTP_TIMEOUT
from .errors import NetworkError, error_from_response
from .timeutil import now_iso

logger = logging.getLogger("core.api")


@dataclass(frozen=True)
class ApiResult:
    """一次 HTTP 调用的结果。

    Attributes:
        ok: 是否 2xx。
        status_code: HTTP 状态码；`0` 表示网络层失败（未拿到响应）。
        code: 业务错误码（成功为 `ok`）。
        message: 中文提示。
        data: 响应 `data` 字段（旧端点可能是数组）。
    """

    ok: bool
    status_code: int
    code: str
    message: str
    data: Any = None

    @property
    def is_network_error(self) -> bool:
        """是否网络层失败（断网 / 超时 / DNS）。"""
        return self.status_code == 0

    @property
    def is_retryable(self) -> bool:
        """是否值得重试（网络错误 / 5xx / 429）。"""
        return self.status_code == 0 or self.status_code >= 500 or self.status_code == 429

    @property
    def is_conflict(self) -> bool:
        """是否 409。"""
        return self.status_code == 409

    @property
    def is_permanent_failure(self) -> bool:
        """是否永久失败（400 / 403 / 404），重试无意义。"""
        return self.status_code in (400, 403, 404)


class ApiClient:
    """服务端 API 客户端。"""

    def __init__(
        self,
        base_url: str,
        *,
        client_id: str,
        token: str = "",
        timeout: float = DEFAULT_HTTP_TIMEOUT,
        http: Optional[Any] = None,
    ) -> None:
        """构造。

        Args:
            base_url: 服务端根地址，如 `http://127.0.0.1:5000`。
            client_id: 设备 ID（必带）。
            token: 会话 token（可空）。
            timeout: 请求超时秒数。
            http: 可注入的 HTTP 传输对象（测试用），需实现 `request(...)`。
        """
        self._base_url = (base_url or "").rstrip("/")
        self._client_id = client_id
        self._token = token
        self._timeout = float(timeout)
        if http is not None:
            self._http = http
        else:
            session = requests.Session()
            # 关闭环境变量代理：公司/校园网的 HTTP_PROXY 会把 127.0.0.1 也劫持走，
            # 表现为"服务端明明启动了却一直 502"。客户端只连自己配置的地址。
            session.trust_env = False
            self._http = session

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------
    @property
    def client_id(self) -> str:
        """当前设备 ID。"""
        return self._client_id

    @property
    def token(self) -> str:
        """当前 token。"""
        return self._token

    def set_token(self, token: str) -> None:
        """更新 token（`/session` 成功后调用）。"""
        self._token = token or ""

    def set_client_id(self, client_id: str) -> None:
        """更新设备 ID。"""
        self._client_id = client_id

    # ------------------------------------------------------------------
    # 底层
    # ------------------------------------------------------------------
    def _url(self, path: str) -> str:
        """拼完整 URL。"""
        return f"{self._base_url}/{path.lstrip('/')}"

    def _headers(self) -> dict[str, str]:
        """构造请求头。"""
        headers = {"X-Client-Id": self._client_id, "Accept": "application/json"}
        if self._token:
            headers["X-Client-Token"] = self._token
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> ApiResult:
        """执行一次请求并归一化为 `ApiResult`。"""
        url = self._url(path)
        clean_params = (
            {key: value for key, value in params.items() if value is not None}
            if params
            else None
        )
        try:
            response = self._http.request(
                method,
                url,
                params=clean_params,
                json=dict(payload) if payload is not None else None,
                headers=self._headers(),
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            logger.warning("%s %s 网络失败: %s", method, path, exc)
            return ApiResult(False, 0, "network_error", "网络好像断了，先记在本地啦")
        return self._normalize(response, method, path)

    def _normalize(self, response: Any, method: str, path: str) -> ApiResult:
        """把 HTTP 响应转成 `ApiResult`。"""
        status = int(getattr(response, "status_code", 0) or 0)
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict) and "code" in body:
            code = str(body.get("code") or "ok")
            message = str(body.get("message") or "")
            data = body.get("data")
        else:
            # 旧端点可能直接返回数组
            code = "ok" if 200 <= status < 300 else "server_error"
            message = ""
            data = body
        ok = 200 <= status < 300
        if not ok:
            logger.warning(
                "%s %s 失败 status=%s code=%s", method, path, status, code
            )
        return ApiResult(ok=ok, status_code=status, code=code, message=message, data=data)

    # ------------------------------------------------------------------
    # E01 / E02
    # ------------------------------------------------------------------
    def health(self) -> ApiResult:
        """E01 健康检查。"""
        return self._request("GET", "/api/v2/health")

    def session(self, nickname: str) -> ApiResult:
        """E02 登记身份并取 token（成功后自动记住 token）。"""
        result = self._request(
            "POST",
            "/api/v2/session",
            payload={"client_id": self._client_id, "nickname": nickname},
        )
        if result.ok and isinstance(result.data, dict):
            self.set_token(str(result.data.get("token") or ""))
        return result

    # ------------------------------------------------------------------
    # E03 / E04 / E05
    # ------------------------------------------------------------------
    def list_bubbles(
        self,
        *,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        emotion: Optional[str] = None,
        q: Optional[str] = None,
        since: Optional[str] = None,
    ) -> ApiResult:
        """E03 公开泡泡列表。"""
        return self._request(
            "GET",
            "/api/v2/bubbles",
            params={
                "limit": limit,
                "cursor": cursor,
                "emotion": emotion,
                "q": q,
                "since": since,
            },
        )

    def create_bubble(
        self,
        *,
        op_id: str,
        content: str,
        emotion_tag: str,
        reply_mode: str,
        bubble_id: Optional[str] = None,
        created_at: Optional[str] = None,
        base_version: int = 0,
    ) -> ApiResult:
        """E04 发布泡泡。"""
        payload: dict[str, Any] = {
            "op_id": op_id,
            "content": content,
            "emotion_tag": emotion_tag,
            "reply_mode": reply_mode,
            "created_at": created_at or now_iso(),
            "base_version": base_version,
        }
        if bubble_id:
            payload["id"] = bubble_id
        return self._request("POST", "/api/v2/bubbles", payload=payload)

    def get_bubble(self, bubble_id: str) -> ApiResult:
        """E05 详情。"""
        return self._request("GET", f"/api/v2/bubbles/{bubble_id}")

    # ------------------------------------------------------------------
    # E06 ~ E08
    # ------------------------------------------------------------------
    def reply(self, bubble_id: str, *, op_id: str, content: str) -> ApiResult:
        """E06 发回复。"""
        return self._request(
            "POST",
            f"/api/v2/bubbles/{bubble_id}/reply",
            payload={"op_id": op_id, "content": content},
        )

    def get_chat(self, bubble_id: str, *, peer: Optional[str] = None) -> ApiResult:
        """E07 读对话。"""
        return self._request(
            "GET", f"/api/v2/bubbles/{bubble_id}/chat", params={"peer": peer}
        )

    def post_chat(
        self, bubble_id: str, *, op_id: str, receiver: str, content: str
    ) -> ApiResult:
        """E08 发对话。"""
        return self._request(
            "POST",
            f"/api/v2/bubbles/{bubble_id}/chat",
            payload={"op_id": op_id, "receiver": receiver, "content": content},
        )

    # ------------------------------------------------------------------
    # E09 ~ E11
    # ------------------------------------------------------------------
    def resolve(self, bubble_id: str, *, op_id: str, base_version: int = 0) -> ApiResult:
        """E09 标记已解决。"""
        return self._request(
            "POST",
            f"/api/v2/bubbles/{bubble_id}/resolve",
            payload={"op_id": op_id, "base_version": base_version},
        )

    def save(
        self, bubble_id: str, *, op_id: str, is_public: bool, base_version: int = 0
    ) -> ApiResult:
        """E10 公开/私密保留。"""
        return self._request(
            "POST",
            f"/api/v2/bubbles/{bubble_id}/save",
            payload={"op_id": op_id, "is_public": bool(is_public), "base_version": base_version},
        )

    def mine(
        self,
        *,
        group: str = "published",
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> ApiResult:
        """E11 我的分组。"""
        return self._request(
            "GET",
            "/api/v2/mine",
            params={"group": group, "limit": limit, "cursor": cursor},
        )


def raise_for_result(result: ApiResult) -> ApiResult:
    """把失败结果转成对应异常（需要异常语义时使用）。

    Args:
        result: `ApiResult`。

    Returns:
        原结果（成功时）。

    Raises:
        NetworkError: 网络层失败。
        ApiError: 服务端返回非 2xx（按状态码选子类）。
    """
    if result.ok:
        return result
    if result.is_network_error:
        raise NetworkError(result.message)
    raise error_from_response(result.status_code, result.code, result.message, result.data)


__all__ = ["ApiResult", "ApiClient", "raise_for_result"]
