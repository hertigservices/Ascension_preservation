"""Read-only desktop view of the collector's redacted status snapshot."""
import re,time,webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import ttk
import intake_jobs

def tail(path):
    try:
        with Path(path).open('rb') as f:
            f.seek(0,2);f.seek(max(0,f.tell()-16384));return f.read().decode('utf-8','replace')
    except OSError:return ''

def rows(work):
    config=intake_jobs.read(Path(work)/'shared-collector.json',{})
    state=Path(config.get('state',Path(work).parent/'upload-private'))
    snapshot=intake_jobs.read(state/'stream-status.json',{})
    result=[]
    entries=snapshot.get('submissions',[])
    if not entries:
        for job in state.glob('*'):
            if not re.fullmatch('[a-f0-9-]{36}',job.name):continue
            outcome=intake_jobs.read(job/'result.json',{});outcome=outcome.get('result',outcome)
            if outcome:
                retention=intake_jobs.read(job/'retention.json',{})
                entries.append({'id':job.name,'status':outcome.get('status','unknown'),'commit_sha':outcome.get('commit'),'created':retention.get('created',0)})
    for entry in entries:
        sid=entry.get('id','')
        if not re.fullmatch('[a-f0-9-]{36}',sid):continue
        job=state/sid;batch=intake_jobs.read(job/'batch.json',{});leader=batch.get('publisher_submission',sid)
        if not re.fullmatch('[a-f0-9-]{36}',leader):leader=sid
        log=state/leader/'publisher.log';stage=entry['status'];text=tail(log) if stage=='processing' else ''
        markers=[('pushing ','Pushing to GitHub'),('publish audit','Privacy audit'),('== export','Exporting'),('== lua','Merging Account data'),('== merge','Merging caches'),('== intake','Reading inputs'),('== consolidating','Consolidating')]
        found=[(text.rfind(marker),label) for marker,label in markers if marker in text]
        if found:stage=max(found)[1]
        elif stage=='processing':stage='Validating / preparing'
        result.append({**entry,'stage':stage,'log':str(log if log.exists() else job/'validation.log')})
    for p in (Path(work)/'manual-queue').glob('*.json'):
        entry=intake_jobs.read(p,{})
        result.append({**entry,'id':'manual-'+p.stem,'bytes':None,'modes':['Manual inbox'],'stage':entry.get('status','queued'),'log':str(p.with_suffix('.log'))})
    return sorted(result,key=lambda row:row.get('created',0),reverse=True),snapshot

class ContributionsView(ttk.Frame):
    def __init__(self,parent,work,open_path):
        super().__init__(parent,padding=12);self.work=work;self.open_path=open_path;self.entries={}
        self.note=tk.StringVar(value='Reading collector status...')
        ttk.Label(self,textvariable=self.note,wraplength=900).pack(fill='x',pady=(0,8))
        columns=('received','size','mode','status','stage','commit')
        self.table=ttk.Treeview(self,columns=columns,show='headings',selectmode='browse')
        for key,label,width in zip(columns,['Received','Size','Mode / realm','Status','Current stage','Commit'],[138,72,150,100,190,85]):
            self.table.heading(key,text=label);self.table.column(key,width=width,minwidth=65)
        scroll=ttk.Scrollbar(self,orient='vertical',command=self.table.yview);self.table.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');self.table.pack(fill='both',expand=True)
        actions=ttk.Frame(self);actions.pack(fill='x',pady=(8,0))
        ttk.Button(actions,text='Open selected log',command=self.log).pack(side='left')
        ttk.Button(actions,text='View GitHub commit',command=self.commit).pack(side='left',padx=8)
        ttk.Button(actions,text='Retry failed manual run',command=self.retry).pack(side='left',padx=8)
        ttk.Label(actions,text='Online work continues when this window is closed.').pack(side='left',padx=8)
        self.refresh()
    def selected(self):
        selection=self.table.selection();return self.entries.get(selection[0],{}) if selection else {}
    def log(self):
        entry=self.selected()
        if entry.get('log') and Path(entry['log']).is_file():self.open_path(entry['log'])
    def commit(self):
        sha=self.selected().get('commit_sha','') or ''
        if re.fullmatch('[a-f0-9]{40}',sha):webbrowser.open('https://github.com/hertigservices/ascension-data/commit/'+sha)
    def retry(self):
        sid=self.selected().get('id','')
        if not sid.startswith('manual-'):return
        path=Path(self.work)/'manual-queue'/(sid[7:]+'.json');item=intake_jobs.read(path,{})
        if item.get('status')=='failed':
            item.update(status='queued',attempts=0,next_attempt=0);intake_jobs.write(path,item)
    def refresh(self):
        entries,snapshot=rows(self.work);selection=self.table.selection();self.entries={e['id']:e for e in entries}
        for item in self.table.get_children():
            if item not in self.entries:self.table.delete(item)
        for index,e in enumerate(entries):
            timestamp=e.get('created',0);size=e.get('bytes');values=(time.strftime('%m/%d %H:%M',time.localtime(timestamp)) if timestamp else 'Earlier upload',f'{size/1048576:.1f} MiB' if size else '—',', '.join(e.get('modes',[])) or 'Unknown realm',e.get('status','unknown'),e.get('stage',''),(e.get('commit_sha') or '')[:8])
            if self.table.exists(e['id']):self.table.item(e['id'],values=values)
            else:self.table.insert('','end',iid=e['id'],values=values)
            self.table.move(e['id'],'',index)
        stamp=snapshot.get('checked',0);age=time.time()-stamp
        self.note.set(('Collector status updated '+time.strftime('%H:%M:%S',time.localtime(stamp)) if stamp else 'Collector status not yet available.')+(' — status is stale; collector may be paused or offline.' if age>90 else '')+' Realm names are not collected; detected game modes are shown.')
        self.after(5000,self.refresh)
