# -*- coding: utf-8 -*-
"""
admin_helper.py - 独立管理员任务脚本 (零外部依赖)

用法:
  python admin_helper.py add <target_host> <marker> [cert_path] [flag_file]
  python admin_helper.py remove <target_host> <marker> [flag_file]

此脚本仅使用 Python 内置模块，不依赖任何第三方库。
通过 ShellExecuteW runas 以管理员权限运行。
"""
import os
import sys
import subprocess

HOSTS_FILE = r"C:\Windows\System32\drivers\etc\hosts"

def log(msg):
    """写入调试日志"""
    try:
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "admin_helper_debug.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{msg}\n")
    except:
        pass

def main():
    log(f"=== admin_helper.py started ===")
    log(f"sys.argv = {sys.argv}")
    log(f"cwd = {os.getcwd()}")
    
    if len(sys.argv) < 2:
        log("ERROR: Not enough arguments")
        print("Usage:")
        print("  admin_helper.py <add|remove> <target_host> <marker> [cert_path] [--flag <flag_file>]")
        print("  admin_helper.py caddy_trust <caddy_exe_path> [--flag <flag_file>]")
        return
    
    mode = sys.argv[1]        # "add", "remove", or "caddy_trust"
    
    # --- Caddy Trust 模式 ---
    if mode == "caddy_trust":
        caddy_exe = sys.argv[2] if len(sys.argv) > 2 else None
        if not caddy_exe or not os.path.exists(caddy_exe):
            log(f"ERROR: caddy.exe not found: {caddy_exe}")
            return
        
        # 查找 flag_file
        flag_file = None
        for i, arg in enumerate(sys.argv):
            if arg == "--flag" and i + 1 < len(sys.argv):
                flag_file = sys.argv[i + 1]
                break
        
        log(f"Running caddy trust: {caddy_exe}")
        try:
            result = subprocess.run(
                [caddy_exe, "trust"],
                capture_output=True, text=True, timeout=30,
                cwd=os.path.dirname(caddy_exe)
            )
            log(f"caddy trust stdout: {result.stdout}")
            log(f"caddy trust stderr: {result.stderr}")
            log(f"caddy trust returncode: {result.returncode}")
        except Exception as e:
            log(f"caddy trust failed: {e}")
        
        # 写入完成标志
        if flag_file:
            try:
                os.makedirs(os.path.dirname(flag_file), exist_ok=True)
                with open(flag_file, "w") as f:
                    f.write("done")
                log(f"Flag file created: {flag_file}")
            except Exception as e:
                log(f"ERROR creating flag file: {e}")
        
        log("=== admin_helper.py (caddy_trust) finished ===")
        return
    
    # --- mitmproxy CA 信任模式 ---
    if mode == "mitm_ca_trust":
        cert_path = sys.argv[2] if len(sys.argv) > 2 else None
        if not cert_path or not os.path.exists(cert_path):
            log(f"ERROR: cert not found: {cert_path}")
            return
        
        flag_file = None
        for i, arg in enumerate(sys.argv):
            if arg == "--flag" and i + 1 < len(sys.argv):
                flag_file = sys.argv[i + 1]
                break
        
        log(f"Installing mitmproxy CA: {cert_path}")
        try:
            result = subprocess.run(
                ["certutil", "-addstore", "Root", cert_path],
                capture_output=True, text=True, timeout=30
            )
            log(f"certutil stdout: {result.stdout}")
            log(f"certutil stderr: {result.stderr}")
            log(f"certutil returncode: {result.returncode}")
        except Exception as e:
            log(f"certutil failed: {e}")
        
        if flag_file:
            try:
                os.makedirs(os.path.dirname(flag_file), exist_ok=True)
                with open(flag_file, "w") as f:
                    f.write("done")
                log(f"Flag file created: {flag_file}")
            except Exception as e:
                log(f"ERROR creating flag file: {e}")
        
        log("=== admin_helper.py (mitm_ca_trust) finished ===")
        return
    
    # --- DNS 设置模式 ---
    if mode == "set_dns":
        dns_server = sys.argv[2] if len(sys.argv) > 2 else None
        if not dns_server:
            log("ERROR: DNS server address required")
            return
        
        # 查找 flag_file
        flag_file = None
        for i, arg in enumerate(sys.argv):
            if arg == "--flag" and i + 1 < len(sys.argv):
                flag_file = sys.argv[i + 1]
                break
        
        log(f"Setting DNS for all adapters to: {dns_server}")
        
        # 保存当前 DNS 设置
        state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "tools", "caddy", "_dns_original.json")
        try:
            # 获取所有活动适配器
            result = subprocess.run(
                ["powershell", "-Command",
                 "Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | "
                 "ForEach-Object { $dns = Get-DnsClientServerAddress -InterfaceIndex $_.InterfaceIndex -AddressFamily IPv4; "
                 "[PSCustomObject]@{Name=$_.Name; Index=$_.InterfaceIndex; DNS=$dns.ServerAddresses -join ','} } | "
                 "ConvertTo-Json"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                import json
                original_settings = json.loads(result.stdout)
                if isinstance(original_settings, dict):
                    original_settings = [original_settings]
                
                # 保存原始设置
                os.makedirs(os.path.dirname(state_file), exist_ok=True)
                with open(state_file, 'w') as f:
                    json.dump(original_settings, f, indent=2)
                log(f"Saved original DNS settings for {len(original_settings)} adapters")
                
                # 设置每个适配器的 DNS
                for adapter in original_settings:
                    name = adapter.get('Name', '')
                    if name:
                        try:
                            subprocess.run(
                                ["netsh", "interface", "ip", "set", "dns",
                                 f"name={name}", "static", dns_server, "primary"],
                                capture_output=True, timeout=5
                            )
                            # 添加上游 DNS 作为备用
                            subprocess.run(
                                ["netsh", "interface", "ip", "add", "dns",
                                 f"name={name}", "223.5.5.5", "index=2"],
                                capture_output=True, timeout=5
                            )
                            log(f"Set DNS for adapter '{name}' to {dns_server}")
                        except Exception as e:
                            log(f"Failed to set DNS for '{name}': {e}")
            else:
                log(f"Failed to get adapters: {result.stderr}")
        except Exception as e:
            log(f"set_dns failed: {e}")
        
        # 刷新 DNS 缓存
        try:
            subprocess.run(["ipconfig", "/flushdns"], capture_output=True, timeout=5)
            log("DNS cache flushed")
        except Exception:
            pass
        
        if flag_file:
            try:
                os.makedirs(os.path.dirname(flag_file), exist_ok=True)
                with open(flag_file, "w") as f:
                    f.write("done")
            except Exception as e:
                log(f"ERROR creating flag file: {e}")
        
        log("=== admin_helper.py (set_dns) finished ===")
        return
    
    if mode == "restore_dns":
        # 查找 flag_file
        flag_file = None
        for i, arg in enumerate(sys.argv):
            if arg == "--flag" and i + 1 < len(sys.argv):
                flag_file = sys.argv[i + 1]
                break
        
        log("Restoring DNS settings for all adapters")
        
        state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "tools", "caddy", "_dns_original.json")
        
        try:
            if os.path.exists(state_file):
                import json
                with open(state_file, 'r') as f:
                    original_settings = json.load(f)
                
                for adapter in original_settings:
                    name = adapter.get('Name', '')
                    original_dns = adapter.get('DNS', '')
                    if name:
                        if original_dns and original_dns.strip():
                            # 恢复原始 DNS
                            dns_list = [d.strip() for d in original_dns.split(',') if d.strip()]
                            if dns_list:
                                try:
                                    subprocess.run(
                                        ["netsh", "interface", "ip", "set", "dns",
                                         f"name={name}", "static", dns_list[0], "primary"],
                                        capture_output=True, timeout=5
                                    )
                                    for idx, dns in enumerate(dns_list[1:], 2):
                                        subprocess.run(
                                            ["netsh", "interface", "ip", "add", "dns",
                                             f"name={name}", dns, f"index={idx}"],
                                            capture_output=True, timeout=5
                                        )
                                    log(f"Restored DNS for '{name}' to {original_dns}")
                                except Exception as e:
                                    log(f"Failed to restore DNS for '{name}': {e}")
                            else:
                                # 设为 DHCP 自动获取
                                subprocess.run(
                                    ["netsh", "interface", "ip", "set", "dns",
                                     f"name={name}", "dhcp"],
                                    capture_output=True, timeout=5
                                )
                                log(f"Reset DNS for '{name}' to DHCP")
                        else:
                            # 没有原始 DNS 记录 → 设为 DHCP
                            subprocess.run(
                                ["netsh", "interface", "ip", "set", "dns",
                                 f"name={name}", "dhcp"],
                                capture_output=True, timeout=5
                            )
                            log(f"Reset DNS for '{name}' to DHCP")
                
                # 清理状态文件
                try:
                    os.remove(state_file)
                except Exception:
                    pass
            else:
                log("No saved DNS state found, setting all to DHCP")
                # 获取活动适配器并重置
                result = subprocess.run(
                    ["powershell", "-Command",
                     "Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | "
                     "Select-Object -ExpandProperty Name"],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0:
                    for name in result.stdout.strip().splitlines():
                        name = name.strip()
                        if name:
                            subprocess.run(
                                ["netsh", "interface", "ip", "set", "dns",
                                 f"name={name}", "dhcp"],
                                capture_output=True, timeout=5
                            )
                            log(f"Reset DNS for '{name}' to DHCP")
        except Exception as e:
            log(f"restore_dns failed: {e}")
        
        # 刷新 DNS 缓存
        try:
            subprocess.run(["ipconfig", "/flushdns"], capture_output=True, timeout=5)
            log("DNS cache flushed")
        except Exception:
            pass
        
        if flag_file:
            try:
                os.makedirs(os.path.dirname(flag_file), exist_ok=True)
                with open(flag_file, "w") as f:
                    f.write("done")
            except Exception as e:
                log(f"ERROR creating flag file: {e}")
        
        log("=== admin_helper.py (restore_dns) finished ===")
        return
    
    # --- 赞助者 一体化设置模式 ---
    if mode == "sponsor_setup":
        # 在一次 UAC 提权中完成: hosts 修改 + 停止 dnscache + 设置适配器 DNS
        target_host = sys.argv[2] if len(sys.argv) > 2 else "pastebin.com"
        marker = sys.argv[3] if len(sys.argv) > 3 else "# SlashCoCaddy"
        
        flag_file = None
        for i, arg in enumerate(sys.argv):
            if arg == "--flag" and i + 1 < len(sys.argv):
                flag_file = sys.argv[i + 1]
                break
        
        log(f"sponsor_setup: host={target_host}, marker={marker}")
        import json as _json
        import time as _time
        
        try:
            # Step 1: 修改 hosts
            log("Step 1: Modifying hosts file")
            os.system(f'attrib -r "{HOSTS_FILE}"')
            content = ""
            if os.path.exists(HOSTS_FILE):
                try:
                    with open(HOSTS_FILE, "r", encoding="utf-8") as f:
                        content = f.read()
                except UnicodeDecodeError:
                    with open(HOSTS_FILE, "r", encoding="mbcs") as f:
                        content = f.read()
            
            # 清理旧条目
            if marker in content:
                lines = content.splitlines()
                new_lines = [line for line in lines if marker not in line]
                content = "\n".join(new_lines)
                if content and not content.endswith("\n"):
                    content += "\n"
            
            # 添加新条目
            new_line = f"127.0.0.1 {target_host} {marker}"
            if content and not content.endswith("\n"):
                content += "\n"
            content += new_line + "\n"
            with open(HOSTS_FILE, "w", encoding="utf-8") as f:
                f.write(content)
            log(f"Hosts updated: {new_line}")
            
            # Step 2: 保存当前 DNS 设置并停止 dnscache
            log("Step 2: Saving DNS settings and stopping dnscache")
            state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "tools", "caddy", "_dns_original.json")
            os.makedirs(os.path.dirname(state_file), exist_ok=True)
            
            # 获取活动适配器的 DNS 设置
            try:
                result = subprocess.run(
                    ["powershell", "-Command",
                     "Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | "
                     "ForEach-Object { $dns = Get-DnsClientServerAddress -InterfaceIndex $_.InterfaceIndex -AddressFamily IPv4; "
                     "[PSCustomObject]@{Name=$_.Name; Index=$_.InterfaceIndex; DNS=$dns.ServerAddresses -join ','} } | "
                     "ConvertTo-Json"],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0 and result.stdout.strip():
                    original_settings = _json.loads(result.stdout)
                    if isinstance(original_settings, dict):
                        original_settings = [original_settings]
                    with open(state_file, 'w') as f:
                        _json.dump(original_settings, f, indent=2)
                    log(f"Saved DNS for {len(original_settings)} adapters")
                else:
                    original_settings = []
                    log(f"No adapters found: {result.stderr}")
            except Exception as e:
                original_settings = []
                log(f"Failed to get adapters: {e}")
            
            # 停止 dnscache 服务 (释放端口 53)
            log("Stopping dnscache service")
            subprocess.run(["net", "stop", "dnscache"], capture_output=True, timeout=10)
            _time.sleep(1)
            
            # Step 3: 设置所有适配器 DNS 为 127.0.0.1
            log("Step 3: Setting adapter DNS to 127.0.0.1")
            for adapter in original_settings:
                name = adapter.get('Name', '')
                if name:
                    try:
                        subprocess.run(
                            ["netsh", "interface", "ip", "set", "dns",
                             f"name={name}", "static", "127.0.0.1", "primary"],
                            capture_output=True, timeout=5
                        )
                        log(f"Set DNS for '{name}' to 127.0.0.1")
                    except Exception as e:
                        log(f"Failed for '{name}': {e}")
            
            # 刷新 DNS 缓存
            os.system("ipconfig /flushdns")
            log("DNS cache flushed")
            
        except Exception as e:
            log(f"sponsor_setup error: {e}")
            import traceback
            log(traceback.format_exc())
        
        if flag_file:
            try:
                os.makedirs(os.path.dirname(flag_file), exist_ok=True)
                with open(flag_file, "w") as f:
                    f.write("done")
            except Exception as e:
                log(f"ERROR creating flag: {e}")
        
        log("=== admin_helper.py (sponsor_setup) finished ===")
        return
    
    if mode == "sponsor_teardown":
        # 在一次 UAC 提权中完成: 恢复 hosts + 恢复适配器 DNS + 重启 dnscache
        target_host = sys.argv[2] if len(sys.argv) > 2 else "pastebin.com"
        marker = sys.argv[3] if len(sys.argv) > 3 else "# SlashCoCaddy"
        
        flag_file = None
        for i, arg in enumerate(sys.argv):
            if arg == "--flag" and i + 1 < len(sys.argv):
                flag_file = sys.argv[i + 1]
                break
        
        log(f"sponsor_teardown: host={target_host}, marker={marker}")
        import json as _json
        
        try:
            # Step 1: 恢复 hosts
            log("Step 1: Restoring hosts file")
            os.system(f'attrib -r "{HOSTS_FILE}"')
            content = ""
            if os.path.exists(HOSTS_FILE):
                try:
                    with open(HOSTS_FILE, "r", encoding="utf-8") as f:
                        content = f.read()
                except UnicodeDecodeError:
                    with open(HOSTS_FILE, "r", encoding="mbcs") as f:
                        content = f.read()
            
            if marker in content:
                lines = content.splitlines()
                new_lines = [line for line in lines if marker not in line]
                new_content = "\n".join(new_lines)
                if new_content and not new_content.endswith("\n"):
                    new_content += "\n"
                with open(HOSTS_FILE, "w", encoding="utf-8") as f:
                    f.write(new_content)
                log("Hosts restored")
            
            # Step 2: 恢复适配器 DNS
            log("Step 2: Restoring adapter DNS")
            state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "tools", "caddy", "_dns_original.json")
            
            if os.path.exists(state_file):
                with open(state_file, 'r') as f:
                    original_settings = _json.load(f)
                
                for adapter in original_settings:
                    name = adapter.get('Name', '')
                    original_dns = adapter.get('DNS', '')
                    if name:
                        if original_dns and original_dns.strip():
                            dns_list = [d.strip() for d in original_dns.split(',') if d.strip()]
                            if dns_list:
                                subprocess.run(
                                    ["netsh", "interface", "ip", "set", "dns",
                                     f"name={name}", "static", dns_list[0], "primary"],
                                    capture_output=True, timeout=5
                                )
                                for idx, dns in enumerate(dns_list[1:], 2):
                                    subprocess.run(
                                        ["netsh", "interface", "ip", "add", "dns",
                                         f"name={name}", dns, f"index={idx}"],
                                        capture_output=True, timeout=5
                                    )
                                log(f"Restored DNS for '{name}' to {original_dns}")
                            else:
                                subprocess.run(
                                    ["netsh", "interface", "ip", "set", "dns",
                                     f"name={name}", "dhcp"],
                                    capture_output=True, timeout=5
                                )
                                log(f"Reset DNS for '{name}' to DHCP")
                        else:
                            subprocess.run(
                                ["netsh", "interface", "ip", "set", "dns",
                                 f"name={name}", "dhcp"],
                                capture_output=True, timeout=5
                            )
                            log(f"Reset DNS for '{name}' to DHCP")
                
                try:
                    os.remove(state_file)
                except Exception:
                    pass
            else:
                log("No saved DNS state, resetting to DHCP")
                result = subprocess.run(
                    ["powershell", "-Command",
                     "Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | "
                     "Select-Object -ExpandProperty Name"],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0:
                    for name in result.stdout.strip().splitlines():
                        name = name.strip()
                        if name:
                            subprocess.run(
                                ["netsh", "interface", "ip", "set", "dns",
                                 f"name={name}", "dhcp"],
                                capture_output=True, timeout=5
                            )
                            log(f"Reset DNS for '{name}' to DHCP")
            
            # Step 3: 重启 dnscache 服务
            log("Step 3: Restarting dnscache service")
            subprocess.run(["net", "start", "dnscache"], capture_output=True, timeout=10)
            
            # 刷新 DNS
            os.system("ipconfig /flushdns")
            log("DNS cache flushed, dnscache restarted")
            
        except Exception as e:
            log(f"sponsor_teardown error: {e}")
            import traceback
            log(traceback.format_exc())
        
        if flag_file:
            try:
                os.makedirs(os.path.dirname(flag_file), exist_ok=True)
                with open(flag_file, "w") as f:
                    f.write("done")
            except Exception as e:
                log(f"ERROR creating flag: {e}")
        
        log("=== admin_helper.py (sponsor_teardown) finished ===")
        return
    
    # --- Hosts 修改模式 (原有逻辑) ---
    if len(sys.argv) < 4:
        log("ERROR: Not enough arguments for hosts mode")
        print("Usage: admin_helper.py <add|remove> <target_host> <marker> [cert_path] [--flag <flag_file>]")
        return
    
    target_host = sys.argv[2] # e.g. "pastebin.com"
    marker = sys.argv[3]      # e.g. "# SlashCoSponsorProxy"
    cert_path = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] != "--flag" else None
    
    # 查找 flag_file 参数
    flag_file = None
    for i, arg in enumerate(sys.argv):
        if arg == "--flag" and i + 1 < len(sys.argv):
            flag_file = sys.argv[i + 1]
            break
    
    log(f"mode={mode}, target={target_host}, marker={marker}, cert={cert_path}, flag={flag_file}")
    
    try:
        # --- 证书安装 (仅 add 模式) ---
        if mode == "add" and cert_path and os.path.exists(cert_path):
            log(f"Installing certificate: {cert_path}")
            ret = os.system(f'certutil -addstore Root "{cert_path}"')
            log(f"certutil returned: {ret}")
        
        # --- 移除只读属性 ---
        os.system(f'attrib -r "{HOSTS_FILE}"')
        
        # --- 读取 hosts 文件 ---
        content = ""
        if os.path.exists(HOSTS_FILE):
            try:
                with open(HOSTS_FILE, "r", encoding="utf-8") as f:
                    content = f.read()
            except UnicodeDecodeError:
                try:
                    with open(HOSTS_FILE, "r", encoding="mbcs") as f:
                        content = f.read()
                except:
                    log("ERROR: Cannot read hosts file")
        
        log(f"Hosts file content length: {len(content)}")
        log(f"Hosts file content: {repr(content[:200])}")
        
        if mode == "add":
            # Step 1: 先清理旧条目 (避免 DNS 被 hosts 污染)
            if marker in content:
                lines = content.splitlines()
                new_lines = [line for line in lines if marker not in line]
                content = "\n".join(new_lines)
                if content and not content.endswith("\n"):
                    content += "\n"
                with open(HOSTS_FILE, "w", encoding="utf-8") as f:
                    f.write(content)
                log("Cleaned up stale hosts entry first")
            
            # Step 2: 刷新 DNS (清除缓存中的 127.0.0.1)
            os.system("ipconfig /flushdns")
            log("DNS cache flushed (pre-resolve)")
            import time
            time.sleep(1)  # 等 DNS 缓存完全清除
            
            # Step 3: 用公共 DNS 解析真实 IP (绕过本地 hosts)
            real_ip = None
            try:
                import re as _re
                result = subprocess.run(
                    ["nslookup", target_host, "8.8.8.8"],
                    capture_output=True, text=True, timeout=10
                )
                log(f"nslookup output: {result.stdout}")
                lines_out = result.stdout.split('\n')
                found_answer = False
                for line in lines_out:
                    if 'non-authoritative' in line.lower():
                        found_answer = True
                    if found_answer and 'address' in line.lower():
                        ip_match = _re.search(r'(\d+\.\d+\.\d+\.\d+)', line)
                        if ip_match:
                            ip = ip_match.group(1)
                            if not ip.startswith('8.8.'):
                                real_ip = ip
                                break
            except Exception as ex:
                log(f"nslookup failed: {ex}")
            
            if not real_ip:
                # 备用：直接用 socket (此时 hosts 已经清理了)
                try:
                    import socket
                    real_ip = socket.gethostbyname(target_host)
                    if real_ip == "127.0.0.1":
                        real_ip = None  # 仍然是 loopback，不能用
                except:
                    pass
            
            if not real_ip:
                real_ip = "104.20.67.143"  # Cloudflare fallback
                log(f"Using fallback IP: {real_ip}")
            else:
                log(f"Resolved real IP: {real_ip}")
            
            # Step 4: 保存真实 IP 到文件 (供 slashco.py 读取)
            ip_file = os.path.join(os.path.dirname(flag_file) if flag_file else ".", "real_ip.txt")
            try:
                os.makedirs(os.path.dirname(ip_file), exist_ok=True)
                with open(ip_file, "w") as f:
                    f.write(real_ip)
                log(f"Real IP saved to: {ip_file}")
            except Exception as ex:
                log(f"Failed to save IP file: {ex}")
            
            # Step 5: 添加 hosts 条目
            new_line = f"127.0.0.1 {target_host} {marker}"
            if content and not content.endswith("\n"):
                content += "\n"
            content += new_line + "\n"
            
            log(f"Writing to hosts: {new_line}")
            with open(HOSTS_FILE, "w", encoding="utf-8") as f:
                f.write(content)
            log("Hosts file updated (add)")
                
        elif mode == "remove":
            if marker in content:
                lines = content.splitlines()
                new_lines = [line for line in lines if marker not in line]
                new_content = "\n".join(new_lines)
                if new_content and not new_content.endswith("\n"):
                    new_content += "\n"
                
                with open(HOSTS_FILE, "w", encoding="utf-8") as f:
                    f.write(new_content)
                log("Hosts file updated (remove)")
            else:
                log("Marker not in hosts, nothing to remove")
        
        # --- 刷新 DNS ---
        os.system("ipconfig /flushdns")
        log("DNS cache flushed")

        
    except Exception as e:
        log(f"ERROR: {e}")
        import traceback
        log(traceback.format_exc())
    
    # --- 写入完成标志 ---
    if flag_file:
        try:
            os.makedirs(os.path.dirname(flag_file), exist_ok=True)
            with open(flag_file, "w") as f:
                f.write("done")
            log(f"Flag file created: {flag_file}")
        except Exception as e:
            log(f"ERROR creating flag file: {e}")
    
    log("=== admin_helper.py finished ===")

if __name__ == "__main__":
    main()
