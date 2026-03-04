import winreg
import ctypes
import os
import sys

def reset_network_settings():
    print("正在重置网络代理设置...")
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            0, winreg.KEY_SET_VALUE
        )
        
        # 1. 删除 AutoConfigURL (PAC)
        try:
            winreg.DeleteValue(key, "AutoConfigURL")
            print("- 已删除 AutoConfigURL")
        except FileNotFoundError:
            print("- AutoConfigURL 不存在 (正常)")
            
        # 2. 删除 ProxyServer (手动代理地址)
        try:
            winreg.DeleteValue(key, "ProxyServer")
            print("- 已删除 ProxyServer")
        except FileNotFoundError:
            pass

        # 3. 禁用 ProxyEnable
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        print("- 已禁用 ProxyEnable")
        
        # 4. 清空 ProxyOverride
        try:
            winreg.DeleteValue(key, "ProxyOverride")
            print("- 已清空 ProxyOverride")
        except FileNotFoundError:
            pass

        winreg.CloseKey(key)

        # 5. 通知系统刷新设置 (关键步骤)
        INTERNET_OPTION_SETTINGS_CHANGED = 39
        INTERNET_OPTION_REFRESH = 37
        ctypes.windll.Wininet.InternetSetOptionW(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
        ctypes.windll.Wininet.InternetSetOptionW(0, INTERNET_OPTION_REFRESH, 0, 0)
        print("- 已通知系统刷新设置")
        
        print("\n✅ 网络设置已成功重置！")
        return True
    except Exception as e:
        print(f"\n❌ 重置失败: {e}")
        return False

if __name__ == "__main__":
    reset_network_settings()
