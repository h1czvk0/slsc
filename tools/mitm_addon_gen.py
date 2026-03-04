# -*- coding: utf-8 -*-
# Auto-generated mitmproxy addon for sponsor list override
from mitmproxy import http

CONTENT_FILE = r"E:/program/slashco/tools/mitm_sponsors.txt"

class SponsorOverrideAddon:
    def response(self, flow: http.HTTPFlow):
        if (flow.request.pretty_host == "pastebin.com" and
                "/raw/2WVJpW1N" in flow.request.path):
            try:
                with open(CONTENT_FILE, "r", encoding="utf-8") as f:
                    modified = f.read()
                flow.response.content = modified.encode("utf-8")
                flow.response.headers["content-type"] = "text/plain; charset=utf-8"
                flow.response.headers["content-length"] = str(len(flow.response.content))
                # 先删除编码头，防止干扰 (虽然赋值 .text 会自动处理，但保险起见)
                if "content-encoding" in flow.response.headers:
                    del flow.response.headers["content-encoding"]
                
                # 使用 .text 赋值，mitmproxy 会自动处理编码和压缩
                # 这样可以避免 GZIP 响应被当做明文发送导致乱码
                flow.response.text = modified
                
                # 显式设置 Content-Type
                flow.response.headers["content-type"] = "text/plain; charset=utf-8"
                
                # 禁用缓存，防止关闭软件后浏览器显示旧的修改内容
                flow.response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
                flow.response.headers["Pragma"] = "no-cache"
                flow.response.headers["Expires"] = "0"
            except Exception:
                pass

addons = [SponsorOverrideAddon()]
