"""Generate the authenticated nginx configuration for the native runtime."""

import os
from pathlib import Path

from frigate.runtime.paths import RuntimePaths


def _nginx_path(path: Path | str) -> str:
    """Quote a path for use as an nginx directive argument."""
    value = str(path)
    if "\n" in value or "\r" in value:
        raise ValueError("Nginx paths cannot contain newlines")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def build_nginx_config(
    paths: RuntimePaths,
    web_root: Path,
    *,
    listen_port: int = 8971,
    listen_host: str = "0.0.0.0",
    tls_certificate: Path | None = None,
    tls_private_key: Path | None = None,
    tls_enabled: bool = True,
    mime_types_path: Path = Path("/opt/homebrew/etc/nginx/mime.types"),
) -> str:
    """Build an authenticated native reverse proxy configuration."""
    if not 1 <= listen_port <= 65535:
        raise ValueError("Native web port must be between 1 and 65535")
    if listen_host not in ("0.0.0.0", "127.0.0.1"):
        raise ValueError("Native web host must be 0.0.0.0 or 127.0.0.1")
    if tls_enabled and (tls_certificate is None or tls_private_key is None):
        raise ValueError("Native TLS requires a certificate and private key")

    nginx_pid = _nginx_path(paths.runtime_dir / "nginx.pid")
    proxy_cache = _nginx_path(paths.cache_dir / "nginx" / "proxy")
    client_cache = _nginx_path(paths.cache_dir / "nginx" / "client")
    auth_request = _nginx_path(paths.runtime_dir / "auth-request.conf")
    proxy_headers = _nginx_path(paths.runtime_dir / "proxy-headers.conf")
    cache = _nginx_path(f"{paths.cache_dir}/")
    clips = _nginx_path(f"{paths.media_dir / 'clips'}/")
    recordings = _nginx_path(f"{paths.media_dir / 'recordings'}/")
    exports = _nginx_path(f"{paths.media_dir / 'exports'}/")
    web = _nginx_path(web_root)
    mime_types = _nginx_path(mime_types_path)
    if tls_enabled:
        listen = f"listen {listen_host}:{listen_port} ssl;"
        tls = f"""
        ssl_certificate {_nginx_path(tls_certificate)};
        ssl_certificate_key {_nginx_path(tls_private_key)};
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_session_timeout 1d;
        ssl_session_cache shared:FrigateTLS:10m;
        ssl_session_tickets off;
"""
    else:
        listen = f"listen {listen_host}:{listen_port};"
        tls = ""

    return f"""daemon off;
worker_processes 1;
pid {nginx_pid};
error_log stderr info;

events {{
    worker_connections 1024;
}}

http {{
    include {mime_types};
    default_type application/octet-stream;
    access_log off;
    proxy_temp_path {proxy_cache};
    client_body_temp_path {client_cache};
    client_max_body_size 20m;

    map $http_upgrade $connection_upgrade {{
        default upgrade;
        '' close;
    }}

    upstream frigate_api {{ server 127.0.0.1:5001; }}
    upstream mqtt_ws {{ server 127.0.0.1:5002; }}
    upstream jsmpeg {{ server 127.0.0.1:8082; }}
    upstream go2rtc {{ server 127.0.0.1:1984; }}

    server {{
        {listen}
        server_name _;
{tls}
        root {web};

        vod_base_url '';
        vod_segments_base_url '';
        vod_mode mapped;
        vod_max_mapping_response_size 1m;
        vod_upstream_location /api;
        vod_align_segments_to_key_frames on;
        vod_manifest_segment_durations_mode accurate;
        vod_ignore_edit_list on;
        vod_segment_duration 10000;
        vod_open_file_thread_pool default;
        vod_metadata_cache metadata_cache 64m;
        vod_mapping_cache mapping_cache 5m 10m;

        location = /auth {{
            internal;
            proxy_pass http://frigate_api/auth;
            proxy_pass_request_body off;
            proxy_pass_request_headers off;
            proxy_set_header X-Original-Method $request_method;
            proxy_set_header X-Original-URL $scheme://$http_host$request_uri;
            proxy_set_header X-Server-Port $server_port;
            proxy_set_header Content-Length "";
            proxy_set_header Authorization $http_authorization;
            proxy_set_header Cookie $http_cookie;
            proxy_set_header X-CSRF-TOKEN "1";
        }}

        location = /api/login {{
            proxy_pass http://frigate_api/login;
            include {proxy_headers};
        }}

        location = /api/logout {{
            proxy_pass http://frigate_api/logout;
            include {proxy_headers};
        }}

        location = /api/auth/first_time_login {{
            proxy_pass http://frigate_api/auth/first_time_login;
            include {proxy_headers};
        }}

        location /api/ {{
            include {auth_request};
            proxy_pass http://frigate_api/;
            include {proxy_headers};
        }}

        location /ws {{
            include {auth_request};
            proxy_pass http://mqtt_ws/;
            include {proxy_headers};
        }}

        location /live/jsmpeg/ {{
            include {auth_request};
            proxy_pass http://jsmpeg/;
            include {proxy_headers};
        }}

        location = /live/mse/api/ws {{
            include {auth_request};
            proxy_pass http://go2rtc/api/ws;
            include {proxy_headers};
        }}

        location = /live/webrtc/api/ws {{
            include {auth_request};
            proxy_pass http://go2rtc/api/ws;
            include {proxy_headers};
        }}

        location /cache/ {{
            internal;
            alias {cache};
        }}

        location /clips/ {{
            include {auth_request};
            alias {clips};
        }}

        location /recordings/ {{
            include {auth_request};
            alias {recordings};
        }}

        location /exports/ {{
            include {auth_request};
            alias {exports};
        }}

        location /vod/ {{
            include {auth_request};
            aio threads;
            vod hls;
            vod_hls_container_format fmp4;
            secure_token $args;
            secure_token_types application/vnd.apple.mpegurl;
            add_header Cache-Control "no-store";
            expires off;
            keepalive_disable safari;
        }}

        location / {{
            add_header Cache-Control "no-store";
            expires off;

            location /assets/ {{
                access_log off;
                expires 1y;
                add_header Cache-Control "public";
            }}

            location /fonts/ {{
                access_log off;
                expires 1y;
                add_header Cache-Control "public";
            }}

            location /locales/ {{
                access_log off;
                add_header Cache-Control "public";
            }}

            sub_filter 'href="/BASE_PATH/' 'href="/';
            sub_filter 'url(/BASE_PATH/' 'url(/';
            sub_filter '"/BASE_PATH/dist/' '"/dist/';
            sub_filter '"/BASE_PATH/js/' '"/js/';
            sub_filter '"/BASE_PATH/assets/' '"/assets/';
            sub_filter '"/BASE_PATH/locales/' '"/locales/';
            sub_filter '"/BASE_PATH/monacoeditorwork/' '"/assets/';
            sub_filter 'return"/BASE_PATH/"' 'return window.baseUrl';
            sub_filter '<body>' '<body><script>window.baseUrl="/";</script>';
            sub_filter_types text/css application/javascript application/json;
            sub_filter_once off;

            try_files $uri $uri.html $uri/ /index.html;
        }}
    }}
}}
"""


def write_native_nginx_files(config: str, paths: RuntimePaths) -> Path:
    """Atomically write private nginx configuration and include files."""
    paths.ensure_directories(
        [
            paths.cache_dir / "nginx" / "proxy",
            paths.cache_dir / "nginx" / "client",
            paths.runtime_dir / "logs",
        ]
    )
    config_path = paths.runtime_dir / "nginx.conf"
    auth_path = paths.runtime_dir / "auth-request.conf"
    proxy_path = paths.runtime_dir / "proxy-headers.conf"

    auth = """auth_request /auth;
auth_request_set $user $upstream_http_remote_user;
auth_request_set $role $upstream_http_remote_role;
auth_request_set $groups $upstream_http_remote_groups;
auth_request_set $auth_cookie $upstream_http_set_cookie;
add_header Set-Cookie $auth_cookie;
proxy_set_header Remote-User $user;
proxy_set_header Remote-Role $role;
proxy_set_header Remote-Groups $groups;
"""
    proxy = """proxy_http_version 1.1;
proxy_set_header Host $host;
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection $connection_upgrade;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Forwarded-Host $http_host;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Real-IP $remote_addr;
proxy_read_timeout 360;
proxy_send_timeout 360;
"""

    for destination, content in (
        (config_path, config),
        (auth_path, auth),
        (proxy_path, proxy),
    ):
        temporary = destination.with_suffix(f"{destination.suffix}.tmp")
        try:
            temporary.write_text(content)
            temporary.chmod(0o600)
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()

    return config_path
