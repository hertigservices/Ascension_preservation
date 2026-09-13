"""Simple private intake desktop. Heavy work runs off the UI thread."""
import argparse
import json
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import intake


def describe(value):
    if isinstance(value,str):return value
    if isinstance(value,list):return '\n\n'.join(describe(v) for v in value)
    if not isinstance(value,dict):return str(value)
    summary=value.get('summary')
    if summary:
        lines=[]
        if value.get('source'):lines.append('Source: '+value['source'])
        lines += [f"{summary['documents']:,} preserved file versions · {summary['containers']:,} archives",
                  f"{summary['record_occurrences']:,} record occurrences · {summary['parsed']:,} parsed files",
                  f"{summary['stored_bytes']/1024**2:,.1f} MiB stored across the archive"]
        if summary['held']:lines.append(f"{summary['held']:,} files retained for review or a future reader.")
        else:lines.append('All listed files have been read successfully.')
        for failure in value.get('copy_failures',[]):lines.append('Could not finish preserving '+Path(failure['file']).name+': '+failure['reason'])
        for held in value.get('needs_attention',[]):lines.append('Retained for review: '+str(held.get('detail') or held['status']))
        if value.get('public_export'):lines.append('Approved public records prepared in the export staging folder.')
        return '\n'.join(lines)
    return value.get('detail',value.get('status',json.dumps(value,ensure_ascii=False)))


class App:
    def __init__(self,window,root):
        self.window=window; self.root=Path(root); self.messages=queue.Queue(); self.busy=False
        window.title('Ascension Research Intake'); window.geometry('880x600'); window.minsize(680,420)
        box=ttk.Frame(window,padding=22); box.pack(fill='both',expand=True)
        ttk.Label(box,text='Preserve a donation',font=('Segoe UI',20,'bold')).pack(anchor='w')
        ttk.Label(box,text='Scrapes, databases, logs and archives. Originals stay on your drive. No AI charges.').pack(anchor='w',pady=(6,18))
        row=ttk.Frame(box); row.pack(fill='x')
        ttk.Label(row,text='Source label').pack(side='left')
        self.source=tk.StringVar(value='community-donation')
        ttk.Entry(row,textvariable=self.source,width=32).pack(side='left',padx=10)
        ttk.Label(row,text='Lowercase, e.g. dungeon-scrape-2026').pack(side='left')
        buttons=ttk.Frame(box); buttons.pack(fill='x',pady=16)
        self.controls=[]
        for title,action in [('Add files',self.files),('Add folder',self.folder),('Process drop folder',self.inbox),('Refresh status',self.status)]:
            b=ttk.Button(buttons,text=title,command=action); b.pack(side='left',padx=(0,8)); self.controls.append(b)
        ttk.Label(box,text='Drop folders: one source folder inside '+str(self.root/'inbox'),wraplength=810).pack(anchor='w')
        self.progress=ttk.Progressbar(box,mode='indeterminate'); self.progress.pack(fill='x',pady=12)
        self.text=tk.Text(box,wrap='word',font=('Consolas',10),height=17); self.text.pack(fill='both',expand=True)
        foot=ttk.Frame(box); foot.pack(fill='x',pady=(12,0))
        ttk.Button(foot,text='Open drop folder',command=lambda:self.open('inbox')).pack(side='left')
        ttk.Button(foot,text='Open reports',command=lambda:self.open('reports')).pack(side='left',padx=8)
        ttk.Label(foot,text='Public exports use a source policy. Unknown formats stay preserved.').pack(side='right')
        self.watch_enabled=tk.BooleanVar(value=True)
        ttk.Checkbutton(box,text='Automatically process drop folders while this window is open',variable=self.watch_enabled).pack(anchor='w',pady=(8,0))
        self.status(); window.after(150,self.poll); window.after(20000,self.watch)
    def watch(self):
        if self.watch_enabled.get() and not self.busy:
            import watch
            self.run(lambda:watch.sweep(self.root))
        self.window.after(20000,self.watch)
    def open(self,folder):
        p=self.root/folder; p.mkdir(parents=True,exist_ok=True)
        if os.name=='nt': os.startfile(p)
    def files(self):
        paths=filedialog.askopenfilenames(title='Choose donation files')
        if paths:self.process(paths)
    def folder(self):
        path=filedialog.askdirectory(title='Choose a donation folder')
        if path:self.process([path])
    def inbox(self):
        self.run(lambda:self.sweep())
    def sweep(self):
        reports=[]
        (self.root/'inbox').mkdir(parents=True,exist_ok=True)
        for path in sorted((self.root/'inbox').iterdir()):
            if not path.is_dir(): continue
            with intake.Intake(self.root) as engine: reports.append(engine.ingest(path,path.name))
        return reports or {'status':'Create a source folder in the drop folder, then add donations inside it.'}
    def process(self,paths):
        source=self.source.get().strip()
        def work():
            result=[]
            for path in paths:
                self.messages.put(('progress','Preserving '+Path(path).name+' …'))
                with intake.Intake(self.root) as engine: result.append(engine.ingest(path,source))
            return result
        self.run(work)
    def status(self):
        def work():
            with intake.Intake(self.root) as engine:
                summary=engine.summary()
                held=[dict(r) for r in engine.db.execute("SELECT DISTINCT d.hash,d.status,d.detail FROM documents d WHERE d.status NOT IN ('parsed','container') LIMIT 100")]
                return {'summary':summary,'needs_attention':held}
        self.run(work)
    def run(self,action):
        if self.busy:return
        self.busy=True
        for b in self.controls:b.configure(state='disabled')
        self.progress.start()
        def work():
            try:self.messages.put(('done',action()))
            except Exception as e:self.messages.put(('error',str(e)))
        threading.Thread(target=work,daemon=False).start()
    def poll(self):
        try:
            while True:
                kind,value=self.messages.get_nowait()
                if value != []:
                    self.text.insert('end',describe(value)+'\n\n'); self.text.see('end')
                if kind!='progress':
                    self.busy=False;self.progress.stop()
                    for b in self.controls:b.configure(state='normal')
        except queue.Empty:pass
        self.window.after(150,self.poll)


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--root',type=Path,default=intake.DEFAULT_ROOT); a=ap.parse_args()
    window=tk.Tk(); App(window,a.root); window.mainloop()

if __name__=='__main__':main()
