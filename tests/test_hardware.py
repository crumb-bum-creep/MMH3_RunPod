from mmh3.hardware import select_profile, HardwareInfo
from pathlib import Path

def test_5090_profile(tmp_path: Path):
    p=tmp_path/'p.yaml'
    p.write_text('profiles:\n  rtx_5090:\n    match:\n      gpu_name_contains: ["RTX 5090"]\n  fallback: {}\n')
    name,_=select_profile(HardwareInfo(gpu_name='NVIDIA GeForce RTX 5090'),p)
    assert name=='rtx_5090'

def test_6000_profile(tmp_path: Path):
    p=tmp_path/'p.yaml'
    p.write_text('profiles:\n  pro:\n    match:\n      gpu_name_contains: ["RTX PRO 6000"]\n  fallback: {}\n')
    name,_=select_profile(HardwareInfo(gpu_name='NVIDIA RTX PRO 6000 Blackwell Server Edition'),p)
    assert name=='pro'
