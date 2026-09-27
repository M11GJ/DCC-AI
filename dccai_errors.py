"""Shared public errors and correlation logs for DCC AI. Never log request payloads."""
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

MESSAGES = {
    'unsupported_image': '画像を読み込めませんでした。過去の添付も含め、PNG・JPEG・WebP・GIF形式で再添付してください。',
    'context_limit': '会話の長さが上限に達しました。新しいチャットで続けてください。',
    'payload_too_large': '送信データが大きすぎます。画像や添付ファイルを減らしてください。',
    'quota_exceeded': 'AIサービスの利用枠に達しています。管理者にお問い合わせください。',
    'authentication_error': 'AIサービスとの接続認証に失敗しました。管理者にお問い合わせください。',
    'permission_denied': 'この操作を実行する権限がありません。',
    'model_error': 'AIモデルの設定に問題があります。管理者にお問い合わせください。',
    'tool_error': 'ツールの実行処理に失敗しました。時間をおいて再試行してください。',
    'invalid_request': '送信内容を処理できませんでした。入力や添付を確認してください。',
    'search_limited': '検索先でアクセスが制限されています。時間をおいて再試行してください。',
    'retrieval_error': '情報を取得できませんでした。URLやアクセス権を確認してください。',
    'timeout': '応答が時間内に届きませんでした。時間をおいて再試行してください。',
    'connection_error': '通信が途中で切れました。時間をおいて再試行してください。',
    'rate_limited': 'ただいま混み合っています。少し時間をおいて再試行してください。',
    'service_error': 'AIサービスで一時的な障害が発生しています。時間をおいて再試行してください。',
    'content_rejected': '送信内容がAIサービス側で受け付けられませんでした。内容を確認してください。',
    'internal_error': '処理に失敗しました。再試行しても続く場合は、問い合わせ番号を管理者にお伝えください。',
}


def _details(error, status=None):
    response = getattr(error, 'response', None)
    if response is not None:
        status = status or getattr(response, 'status_code', None)
        try:
            detail = response.text
        except Exception:
            detail = str(error)
    elif isinstance(error, (dict, list)):
        detail = json.dumps(error, ensure_ascii=False)
    elif isinstance(error, bytes):
        detail = error.decode('utf-8', errors='replace')
    else:
        detail = str(error)
    if isinstance(error, BaseException):
        detail = type(error).__name__ + ': ' + detail
    return detail, status


def classify_error(error, status=None, source=''):
    detail, status = _details(error, status)
    t = detail.lower()
    # Inspect the actual cause before HTTP status and LiteLLM's fallback wrapper.
    rules = [
        ('unsupported_image', ('unsupported image', 'invalid image', 'image format', 'image is valid', 'image decode', 'image_url is invalid')),
        ('context_limit', ('context_length_exceeded', 'maximum context', 'context window', 'contextwindowexceeded', 'too many tokens', 'context length')),
        ('payload_too_large', ('payload too large', 'request entity too large', 'request body too large', 'image too large')),
        ('quota_exceeded', ('insufficient_quota', 'insufficient balance', 'credit balance', 'quota exceeded', 'budget exceeded', 'billing hard limit', 'payment required')),
        ('search_limited', ('captcha', 'bot detection', 'robots denied')),
        ('tool_error', ('tool_call', 'tool call', 'reasoning_content', 'ツール呼び出し', 'ツール引数', '提供されていないツール')),
        ('content_rejected', ('content_filter', 'content policy', 'safety policy')),
        ('model_error', ('model not found', 'model does not exist', 'model is not available', 'unsupported parameter', 'unknown model')),
        ('authentication_error', ('invalid api key', 'incorrect api key', 'authenticationerror', 'authentication failed')),
        ('timeout', ('timeout', 'timed out', 'deadline exceeded')),
        ('connection_error', ('connecterror', 'readerror', 'remoteprotocolerror', 'connection reset', 'connection refused', 'name resolution', '完了前に切断')),
    ]
    for code, terms in rules:
        if any(term in t for term in terms):
            return code
    if status == 413: return 'payload_too_large'
    if status == 402: return 'quota_exceeded'
    if status == 401: return 'authentication_error'
    if status == 403: return 'permission_denied'
    if status == 429: return 'rate_limited'
    if status in (408, 504): return 'timeout'
    if status and status >= 500: return 'service_error'
    if source in ('webui_tool', 'retrieval'): return 'retrieval_error'
    if status in (400, 422): return 'invalid_request'
    return 'internal_error'


def redact_detail(text):
    text = re.sub(r'(?i)data:[^\s;,]+;base64,[a-z0-9+/=\r\n]+', '[IMAGE REDACTED]', text)
    text = re.sub(r'(?i)\bBearer\s+[^\s"\'<>]+', 'Bearer [REDACTED]', text)
    text = re.sub(r'\bsk-[A-Za-z0-9_-]+', '[KEY REDACTED]', text)
    text = re.sub(r'(?i)((?:api[_-]?key|access_token|refresh_token|authorization|password|secret)\s*["\']?\s*[:=]\s*["\']?)[^\s,;"\'&}]+', r'\1[REDACTED]', text)
    text = re.sub(r'(https?://)[^/\s:@]+:[^/\s@]+@', r'\1[REDACTED]@', text)
    return text[:32768]


def public_error(error, status=None, source='dccai'):
    # Avoid replacing an already classified error's correlation number.
    if isinstance(error, dict) and error.get('request_id') and error.get('code') in MESSAGES:
        return error
    detail, status = _details(error, status)
    code = classify_error(error, status, source)
    request_id = 'DCC-' + uuid.uuid4().hex[:12].upper()
    record = {'timestamp': datetime.now(timezone.utc).isoformat(), 'request_id': request_id,
              'source': source, 'status': status, 'code': code, 'detail': redact_detail(detail)}
    line = json.dumps(record, ensure_ascii=False)
    logging.getLogger('dccai.errors').error(line)
    configured = os.getenv('DCCAI_ERROR_LOG')
    candidates = [Path(configured)] if configured else [Path('/app/backend/data/dccai_errors.jsonl'), Path('/webui-data/dccai_errors.jsonl')]
    for path in candidates:
        if not path.parent.is_dir():
            continue
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, 'a') as f:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX)
                f.write(line + '\n')
            break
        except OSError:
            logging.getLogger('dccai.errors').warning('Could not append correlation log: %s', request_id)
    return {'message': f'⚠️ {MESSAGES[code]}\n問い合わせ番号：{request_id}',
            'type': code, 'code': code, 'request_id': request_id}
