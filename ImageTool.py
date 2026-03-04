import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from PIL import Image, ImageTk, ImageGrab
import json
import os
import hashlib
import io
import threading

# 配置
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IMG_DIR = os.path.join(BASE_DIR, "img_assets")
JSON_FILE = os.path.join(BASE_DIR, "images.json")
LOC_FILE = os.path.join(BASE_DIR, "locations.json")
# MAX_WIDTH = 600  <-- 已移除分辨率限制
WEBP_QUALITY = 80 #略微提高画质

class ImageManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("SlashCo 图片与位置管理工具 (无限制版)")
        self.root.geometry("1400x900")
        
        self.current_pos_raw = None
        self.original_image = None
        self.tk_image = None
        self.crop_start = None
        self.crop_end = None
        self.rect_id = None
        self.ratio = 1.0
        
        self.mappings = {} 
        self.locations = {}
        self.notes = {}  # 图片备注 {文件名: 备注} 
        
        # 确保目录存在
        if not os.path.exists(IMG_DIR):
            try: os.makedirs(IMG_DIR)
            except: pass
            
        self.load_data()
        self.setup_ui()
        
    def load_data(self):
        try:
            if os.path.exists(LOC_FILE):
                with open(LOC_FILE, 'r', encoding='utf-8') as f:
                    self.locations = json.load(f)
        except Exception as e:
            messagebox.showwarning("警告", f"加载 locations.json 失败: {e}")

        try:
            if os.path.exists(JSON_FILE):
                with open(JSON_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.mappings = data.get("mappings", {})
                    self.notes = data.get("notes", {})  # 加载备注
        except Exception as e:
            messagebox.showwarning("警告", f"加载 images.json 失败: {e}")

    def save_locations(self):
        try:
            with open(LOC_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.locations, f, ensure_ascii=False, indent=4)
        except Exception as e:
            messagebox.showerror("错误", f"保存 locations.json 失败: {e}")

    def save_mappings(self):
        try:
            with open(JSON_FILE, 'w', encoding='utf-8') as f:
                json.dump({"mappings": self.mappings, "notes": self.notes}, f, ensure_ascii=False, indent=4)
        except Exception as e:
            messagebox.showerror("错误", f"保存 images.json 失败: {e}")

    def setup_ui(self):
        self.style = ttk.Style()
        self.style.configure("Treeview", font=("微软雅黑", 9), rowheight=25)
        self.style.map("Treeview", 
            background=[("selected", "#d35400"), ("!focus", "selected", "#d35400")], 
            foreground=[("selected", "white"), ("!focus", "selected", "white")]
        )

        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="工具", menu=file_menu)
        file_menu.add_command(label="清理未引用的图片(Purge)", command=self.purge_unused_images)

        # --- 左侧 ---
        left_panel = ttk.Frame(self.root, padding=10)
        left_panel.pack(side=tk.LEFT, fill=tk.Y)
        
        top_bar = ttk.Frame(left_panel)
        top_bar.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(top_bar, text="位置列表").pack(side=tk.LEFT)
        ttk.Button(top_bar, text="+ 新增", command=self.add_new_location, width=8).pack(side=tk.RIGHT)
        
        search_var = tk.StringVar()
        search_var.trace("w", lambda *args: self.filter_list(search_var.get()))
        self.search_var = search_var
        ttk.Entry(left_panel, textvariable=search_var).pack(fill=tk.X, pady=5)

        filter_frame = ttk.Frame(left_panel)
        filter_frame.pack(fill=tk.X, pady=(0, 5))
        self.show_no_img_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(filter_frame, text="仅显示无图片", variable=self.show_no_img_var, command=lambda: self.filter_list(self.search_var.get())).pack(side=tk.LEFT)

        list_frame = ttk.Frame(left_panel)
        list_frame.pack(fill=tk.BOTH, expand=True)
        v_scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL)
        v_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree = ttk.Treeview(list_frame, columns=("Raw", "CN", "Status"), show="headings", 
                                 yscrollcommand=v_scroll.set, height=30)
        self.tree.heading("Raw", text="位置原名")
        self.tree.heading("CN", text="中文翻译")
        self.tree.heading("Status", text="图")
        self.tree.column("Raw", width=180)
        self.tree.column("CN", width=120)
        self.tree.column("Status", width=40, anchor=tk.CENTER)
        
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        v_scroll.config(command=self.tree.yview)
        
        self.tree.bind("<<TreeviewSelect>>", self.on_select_pos)
        
        self.list_menu = tk.Menu(self.root, tearoff=0)
        self.list_menu.add_command(label="删除位置", command=self.delete_location)
        self.tree.bind("<Button-3>", self.show_list_menu)

        # --- 中间 ---
        mid_panel = ttk.Frame(self.root, padding=10)
        mid_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        info_frame = ttk.LabelFrame(mid_panel, text="位置编辑", padding=10)
        info_frame.pack(fill=tk.X, pady=(0, 10))
        
        row1 = ttk.Frame(info_frame)
        row1.pack(fill=tk.X, pady=2)
        ttk.Label(row1, text="原名:", width=6).pack(side=tk.LEFT)
        self.ent_raw = ttk.Entry(row1, state="readonly")
        self.ent_raw.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        row2 = ttk.Frame(info_frame)
        row2.pack(fill=tk.X, pady=2)
        ttk.Label(row2, text="中文:", width=6).pack(side=tk.LEFT)
        self.var_cn = tk.StringVar()
        self.ent_cn = ttk.Entry(row2, textvariable=self.var_cn)
        self.ent_cn.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row2, text="保存", command=self.save_translation_edit, width=6).pack(side=tk.LEFT, padx=5)

        config_frame = ttk.LabelFrame(mid_panel, text="图片关联", padding=10)
        config_frame.pack(fill=tk.X, pady=(0, 10))

        map_row = ttk.Frame(config_frame)
        map_row.pack(fill=tk.X, pady=5)
        ttk.Label(map_row, text="适用地图:").pack(side=tk.LEFT)
        self.map_var = tk.StringVar(value="通用(默认)")
        self.map_combo = ttk.Combobox(map_row, textvariable=self.map_var, state="readonly", width=30)
        self.map_combo['values'] = ["通用(默认)", "旧SlashCo总部", "马龙家的农场", "菲利普斯·韦斯特伍德高中", "伊斯特伍德综合医院", "德尔塔科研机构"]
        self.map_combo.pack(side=tk.LEFT, padx=10)
        self.map_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_view())

        ttk.Label(mid_panel, text="截图操作: Ctrl+V 粘贴 -> 拖拽框选 -> 回车保存", foreground="#7f8c8d").pack(pady=5)

        self.canvas_container = ttk.Frame(mid_panel, relief="sunken", borderwidth=2)
        self.canvas_container.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(self.canvas_container, bg="#2c3e50")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<ButtonPress-1>", self.on_drag_start)
        self.canvas.bind("<B1-Motion>", self.on_dragging)
        self.canvas.bind("<ButtonRelease-1>", self.on_drag_end)

        btn_frame = ttk.Frame(mid_panel)
        btn_frame.pack(fill=tk.X, pady=10)
        ttk.Button(btn_frame, text="添加图片 (回车)", command=self.save_mapping).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="删除关联", command=self.delete_mapping).pack(side=tk.RIGHT, padx=5)

        # --- 多图管理区域 (新增) ---
        multi_img_frame = ttk.LabelFrame(mid_panel, text="已关联图片", padding=5)
        multi_img_frame.pack(fill=tk.X, pady=(0, 10))

        self.img_list = tk.Listbox(multi_img_frame, height=4, font=("Consolas", 9))
        self.img_list.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.img_list.bind("<<ListboxSelect>>", self.on_img_list_select)

        img_btn_frame = ttk.Frame(multi_img_frame)
        img_btn_frame.pack(side=tk.LEFT, padx=5)
        ttk.Button(img_btn_frame, text="⬆", width=3, command=lambda: self.reorder_image(-1)).pack(pady=2)
        ttk.Button(img_btn_frame, text="⬇", width=3, command=lambda: self.reorder_image(1)).pack(pady=2)
        ttk.Button(img_btn_frame, text="✖", width=3, command=self.delete_image_from_list).pack(pady=2)

        # --- 图片备注区域 ---
        notes_frame = ttk.LabelFrame(mid_panel, text="选中图片备注", padding=5)
        notes_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.note_var = tk.StringVar()
        self.note_entry = tk.Entry(notes_frame, textvariable=self.note_var, font=("微软雅黑", 10), 
                                    relief="solid", bd=1)
        self.note_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        ttk.Button(notes_frame, text="保存备注", command=self.save_note).pack(side=tk.LEFT)
        ttk.Button(notes_frame, text="复制", command=lambda: self.root.clipboard_clear() or 
                   self.root.clipboard_append(self.note_var.get())).pack(side=tk.LEFT, padx=2)

        # --- 右侧 ---
        right_panel = ttk.LabelFrame(self.root, text="图库", padding=5, width=200)
        right_panel.pack(side=tk.RIGHT, fill=tk.Y, padx=10, pady=10)
        
        lib_frame = ttk.Frame(right_panel)
        lib_frame.pack(fill=tk.BOTH, expand=True)
        lib_scroll = ttk.Scrollbar(lib_frame, orient=tk.VERTICAL)
        lib_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.lib_list = tk.Listbox(lib_frame, width=25, yscrollcommand=lib_scroll.set)
        self.lib_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        lib_scroll.config(command=self.lib_list.yview)
        
        self.lib_list.bind("<Double-Button-1>", self.on_lib_double_click)
        ttk.Button(right_panel, text="刷新", command=self.refresh_lib).pack(fill=tk.X, pady=5)

        self.root.bind("<Control-v>", self.on_paste)
        self.root.bind("<Return>", lambda e: self.save_mapping())

        self.refresh_list()
        self.refresh_lib()

    def purge_unused_images(self):
        """清理未引用的孤儿图片"""
        if not messagebox.askyesno("清理图库", "将删除所有未被任何位置引用的图片文件。\n建议在操作前备份。\n是否继续？"):
            return
        
        needed = set(self.mappings.values())
        if not os.path.exists(IMG_DIR): return
        
        files = os.listdir(IMG_DIR)
        count = 0
        for f in files:
            if f not in needed:
                try:
                    os.remove(os.path.join(IMG_DIR, f))
                    count += 1
                except: pass
        self.refresh_lib()
        messagebox.showinfo("完成", f"已清理 {count} 个未引用的文件")

    def add_new_location(self):
        raw = simpledialog.askstring("新增", "位置原名 (英文ID):")
        if not raw: return
        if raw in self.locations:
            messagebox.showinfo("提示", "已存在")
            self.select_item_by_raw(raw)
            return
        cn = simpledialog.askstring("新增", f"[{raw}] 中文翻译:", initialvalue=raw)
        if cn is None: cn = raw
        self.locations[raw] = cn
        self.save_locations()
        self.refresh_list()
        self.select_item_by_raw(raw)

    def delete_location(self):
        sel = self.tree.selection()
        if not sel: return
        raw = self.tree.item(sel[0])['values'][0]
        if messagebox.askyesno("删除", f"删除位置 [{raw}]？\n这将同时删除其翻译信息。"):
            del self.locations[raw]
            self.save_locations()
            self.refresh_list()
            self.current_pos_raw = None
            self.ent_raw.configure(state="normal")
            self.ent_raw.delete(0, tk.END)
            self.ent_raw.configure(state="readonly")
            self.var_cn.set("")

    def save_translation_edit(self):
        if not self.current_pos_raw: return
        new_cn = self.var_cn.get().strip()
        if not new_cn: return
        self.locations[self.current_pos_raw] = new_cn
        self.save_locations()
        sel = self.tree.selection()
        if sel:
            values = list(self.tree.item(sel[0], "values"))
            values[1] = new_cn
            self.tree.item(sel[0], values=values)
        messagebox.showinfo("成功", "翻译已更新")

    def show_list_menu(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.list_menu.post(event.x_root, event.y_root)

    def select_item_by_raw(self, raw):
        for item in self.tree.get_children():
            if self.tree.item(item)['values'][0] == raw:
                self.tree.selection_set(item)
                self.tree.see(item)
                break

    def get_active_key(self):
        if not self.current_pos_raw: return None
        m = self.map_var.get()
        return self.current_pos_raw if m == "通用(默认)" else f"{self.current_pos_raw}|{m}"

    def refresh_list(self):
        self.filter_list("")

    def filter_list(self, query):
        self.tree.delete(*self.tree.get_children())
        query = query.lower()
        map_suffix = "|" + self.map_var.get() if self.map_var.get() != "通用(默认)" else ""
        show_no_img_only = self.show_no_img_var.get() if hasattr(self, "show_no_img_var") else False
        
        all_locs = set(self.locations.keys())
        for k in self.mappings.keys():
            all_locs.add(k.split("|")[0])
        for loc in sorted(list(all_locs)):
            cn = self.locations.get(loc, "")
            if query in loc.lower() or query in cn.lower():
                key = loc if not map_suffix else loc + map_suffix
                has_img = key in self.mappings or loc in self.mappings
                if show_no_img_only and has_img:
                    continue
                status = "●" if has_img else ""
                cn_display = cn if cn else "[未翻译]"
                self.tree.insert("", tk.END, values=(loc, cn_display, status))

    def on_select_pos(self, event):
        sel = self.tree.selection()
        if not sel: return
        vals = self.tree.item(sel[0])['values']
        self.current_pos_raw = vals[0]
        self.ent_raw.configure(state="normal")
        self.ent_raw.delete(0, tk.END)
        self.ent_raw.insert(0, self.current_pos_raw)
        self.ent_raw.configure(state="readonly")
        cn = self.locations.get(self.current_pos_raw, "")
        self.var_cn.set(cn)
        self.refresh_view()

    def refresh_view(self):
        key = self.get_active_key()
        self.img_list.delete(0, tk.END)
        
        if key and key in self.mappings:
            val = self.mappings[key]
            if isinstance(val, str):
                img_files = [val]
            else:
                img_files = val if val else []
            
            for f in img_files:
                self.img_list.insert(tk.END, f)
            
            # 默认选中第一张并预览
            if img_files:
                self.img_list.selection_set(0)
                self.preview_image(img_files[0])
                self.note_var.set(self.notes.get(img_files[0], ""))
                return
        self.clear_canvas()
        self.note_var.set("")

    def preview_image(self, fname):
        """在画布上预览指定文件名的图片"""
        path = os.path.join(IMG_DIR, fname)
        if os.path.exists(path):
            try:
                self.load_pil_image(Image.open(path))
            except:
                self.clear_canvas()
        else:
            self.clear_canvas()

    def on_paste(self, event):
        try:
            img = ImageGrab.grabclipboard()
            if isinstance(img, Image.Image):
                # 粘贴新图片时，清除之前的裁剪框，避免逻辑混乱
                self.load_pil_image(img)
                self.crop_start = None
                self.crop_end = None
                self.rect_id = None
        except: pass

    def load_pil_image(self, pil_img):
        self.original_image = pil_img
        self.root.update()
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw < 20 or ch < 20: return
        iw, ih = pil_img.size
        self.ratio = min(cw/iw, ch/ih, 1.0)
        nw, nh = int(iw*self.ratio), int(ih*self.ratio)
        resized = pil_img.resize((nw, nh), Image.Resampling.LANCZOS)
        self.tk_image = ImageTk.PhotoImage(resized)
        self.canvas.delete("all")
        self.off_x, self.off_y = (cw-nw)//2, (ch-nh)//2
        self.canvas.create_image(self.off_x, self.off_y, anchor="nw", image=self.tk_image)
        self.canvas.create_rectangle(self.off_x, self.off_y, self.off_x+nw, self.off_y+nh, outline="#3498db", width=1)

    def on_drag_start(self, event):
        self.crop_start = (event.x, event.y)
        if self.rect_id: self.canvas.delete(self.rect_id)

    def on_dragging(self, event):
        if not self.crop_start: return
        if self.rect_id: self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(self.crop_start[0], self.crop_start[1], event.x, event.y, outline="#e74c3c", width=2, dash=(4,4))

    def on_drag_end(self, event):
        self.crop_end = (event.x, event.y)

    def save_mapping(self):
        if not self.original_image: return
        key = self.get_active_key()
        if not key:
            messagebox.showwarning("提示", "请先选择一个位置！")
            return

        try:
            # 1. 裁剪逻辑
            if self.crop_start and self.crop_end:
                x1, y1 = self.crop_start
                x2, y2 = self.crop_end
                lx, ty = (min(x1,x2)-self.off_x)/self.ratio, (min(y1,y2)-self.off_y)/self.ratio
                rx, by = (max(x1,x2)-self.off_x)/self.ratio, (max(y1,y2)-self.off_y)/self.ratio
                w, h = self.original_image.size
                crop_img = self.original_image.crop((max(0,lx), max(0,ty), min(w,rx), min(h,by)))
            else:
                crop_img = self.original_image

            # 2. 保存为 WebP
            buf = io.BytesIO()
            crop_img.save(buf, format="WEBP", quality=WEBP_QUALITY)
            data = buf.getvalue()
            fname = hashlib.md5(data).hexdigest() + ".webp"
            
            # 3. 确保目录存在
            if not os.path.exists(IMG_DIR):
                os.makedirs(IMG_DIR)
            
            with open(os.path.join(IMG_DIR, fname), "wb") as f:
                f.write(data)
            
            # 4. 追加到列表 (多图支持)
            current = self.mappings.get(key)
            if current is None:
                self.mappings[key] = [fname]
            elif isinstance(current, str):
                # 旧格式: 字符串 -> 转为列表并追加
                if current == fname:
                    pass # 重复图片，不追加
                else:
                    self.mappings[key] = [current, fname]
            else:
                # 已是列表
                if fname not in current:
                    current.append(fname)
                    self.mappings[key] = current
            
            self.save_mappings()
            self.refresh_view()
            self.refresh_list()
            self.refresh_lib()
            self.clear_canvas() # 清空画布，准备下一张
            
        except Exception as e:
            messagebox.showerror("保存失败", f"无法保存图片: {e}")

    def delete_mapping(self):
        key = self.get_active_key()
        if key in self.mappings:
            del self.mappings[key]
            self.save_mappings()
            self.refresh_view()
            self.refresh_list()

    def refresh_lib(self):
        self.lib_list.delete(0, tk.END)
        if os.path.exists(IMG_DIR):
            for f in sorted(os.listdir(IMG_DIR)):
                if f.lower().endswith(('.jpg', '.png', '.webp')): 
                    self.lib_list.insert(tk.END, f)

    def on_lib_double_click(self, event):
        """双击图库中的图片，将其添加到当前位置的图片列表"""
        sel = self.lib_list.curselection()
        key = self.get_active_key()
        if sel and key:
            fname = self.lib_list.get(sel[0])
            current = self.mappings.get(key)
            if current is None:
                self.mappings[key] = [fname]
            elif isinstance(current, str):
                if current != fname:
                    self.mappings[key] = [current, fname]
            else:
                if fname not in current:
                    current.append(fname)
            self.save_mappings()
            self.refresh_view()
            self.refresh_list()

    def reorder_image(self, direction):
        """上移(-1)或下移(+1)选中的图片"""
        key = self.get_active_key()
        if not key or key not in self.mappings:
            return
        
        sel = self.img_list.curselection()
        if not sel:
            return
        
        idx = sel[0]
        val = self.mappings[key]
        
        # 确保是列表
        if isinstance(val, str):
            return # 单图无法排序
        
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(val):
            return
        
        # 交换
        val[idx], val[new_idx] = val[new_idx], val[idx]
        self.mappings[key] = val
        self.save_mappings()
        self.refresh_view()
        
        # 保持选中状态
        self.img_list.selection_clear(0, tk.END)
        self.img_list.selection_set(new_idx)

    def delete_image_from_list(self):
        """从当前位置的图片列表中删除选中的图片"""
        key = self.get_active_key()
        if not key or key not in self.mappings:
            return
        
        sel = self.img_list.curselection()
        if not sel:
            return
        
        idx = sel[0]
        val = self.mappings[key]
        
        if isinstance(val, str):
            # 单图，直接删除整个映射
            del self.mappings[key]
        else:
            # 多图，删除指定项
            if len(val) <= 1:
                del self.mappings[key]
            else:
                val.pop(idx)
                self.mappings[key] = val
        
        self.save_mappings()
        self.refresh_view()
        self.refresh_list()

    def on_img_list_select(self, event):
        """选中图片列表中的项目时，加载其备注并预览图片"""
        sel = self.img_list.curselection()
        if not sel:
            self.note_var.set("")
            return
        
        fname = self.img_list.get(sel[0])
        self.note_var.set(self.notes.get(fname, ""))
        self.preview_image(fname)  # 预览选中的图片

    def save_note(self):
        """保存当前选中图片的备注"""
        sel = self.img_list.curselection()
        if not sel:
            messagebox.showwarning("提示", "请先在图片列表中选择一张图片")
            return
        
        fname = self.img_list.get(sel[0])
        note = self.note_var.get().strip()
        
        if note:
            self.notes[fname] = note
        elif fname in self.notes:
            del self.notes[fname]  # 清空备注时删除
        
        self.save_mappings()
        messagebox.showinfo("成功", f"备注已保存: {fname[:20]}...")

    def clear_canvas(self):
        self.canvas.delete("all")
        self.original_image = None
        self.crop_start = None
        self.crop_end = None
        self.rect_id = None

if __name__ == "__main__":
    root = tk.Tk()
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except: pass
    app = ImageManagerApp(root)
    root.mainloop()
