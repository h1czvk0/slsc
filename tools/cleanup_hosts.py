import os
import sys
import ctypes

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def cleanup_hosts():
    hosts_path = r"C:\Windows\System32\drivers\etc\hosts"
    print(f"正在检查 hosts 文件: {hosts_path}")
    
    try:
        with open(hosts_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"读取 hosts 失败: {e}")
        return False

    new_lines = []
    modified = False
    for line in lines:
        if "pastebin.com" in line.lower() and ("slashco" in line.lower() or "127.0.0.1" in line):
            print(f"发现并移除条目: {line.strip()}")
            modified = True
        else:
            new_lines.append(line)
            
    if modified:
        if not is_admin():
            print("需要管理员权限来写入 hosts 文件。")
            # 尝试提权运行
            ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, __file__, None, 1)
            return True # 假设提权成功

        try:
            with open(hosts_path, 'w', encoding='utf-8') as f:
                f.writelines(new_lines)
            print("✅ hosts 文件已清理。")
            os.system("ipconfig /flushdns")
            return True
        except Exception as e:
            print(f"❌ 写入 hosts 失败: {e}")
            return False
    else:
        print("✅ hosts 文件干净，无需清理。")
        return True

if __name__ == "__main__":
    cleanup_hosts()
