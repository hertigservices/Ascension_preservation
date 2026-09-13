"""Opt-in real disk/memory benchmark; synthetic fixtures are removed on completion."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import tempfile
import time
import intake


def peak_memory():
    if os.name=='nt':
        class Counters(ctypes.Structure):
            _fields_=[('cb',ctypes.c_ulong),('faults',ctypes.c_ulong)]+[(x,ctypes.c_size_t) for x in ('peak','working','paged_peak','paged','nonpaged_peak','nonpaged','pagefile','pagefile_peak')]
        counters=Counters();counters.cb=ctypes.sizeof(counters)
        kernel=ctypes.windll.kernel32;kernel.GetCurrentProcess.restype=ctypes.c_void_p
        process=kernel.GetCurrentProcess()
        ctypes.windll.psapi.GetProcessMemoryInfo.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ulong]
        if not ctypes.windll.psapi.GetProcessMemoryInfo(process,ctypes.byref(counters),counters.cb):raise OSError('Memory measurement failed')
        return counters.peak
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--parent',type=Path,required=True);ap.add_argument('--report',type=Path,required=True);a=ap.parse_args()
    with tempfile.TemporaryDirectory(prefix='research-intake-benchmark-',dir=a.parent) as directory:
        base=Path(directory);data=base/'two-gib.jsonl';line=intake.encoded({'id':'7','name':'Synthetic capacity test','payload':'x'*(2*1024*1024)})+b'\n'
        with data.open('wb') as f:
            for _ in range(1025):f.write(line)
        del line
        size=data.stat().st_size;print('Fixture bytes:',size,flush=True);start=time.monotonic()
        with intake.Intake(base/'private') as engine:
            report=engine.ingest(data,'capacity-test')
            summary=engine.summary();first=time.monotonic()-start;start=time.monotonic()
            repeat=engine.ingest(data,'capacity-test')
            elapsed=time.monotonic()-start
            assert summary['record_occurrences']==1025 and summary['unique_record_payloads']==1,summary
            assert report['status']=='preserved' and repeat['status']=='preserved'
            result={'input_bytes':size,'first_seconds':round(first,2),'repeat_seconds':round(elapsed,2),'peak_working_set_bytes':peak_memory(),'summary':summary,'api_calls':0,'synthetic_files_removed_on_completion':True}
            intake.atomic_json(a.report,result);print(json.dumps(result,indent=2),flush=True)
if __name__=='__main__':main()
