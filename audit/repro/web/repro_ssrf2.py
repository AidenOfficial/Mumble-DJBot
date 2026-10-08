import os
import sys, requests
sys.path.insert(0,os.getcwd())
hits=[]
_orig=requests.head
def tap(url,*a,**k):
    hits.append(url); raise requests.exceptions.ConnectionError()
requests.head=tap; requests.get=tap
import media.radio
# Direct exercise of the exact builder the web /post add_radio reaches:
try:
    media.radio.radio_item_builder(url='http://169.254.169.254/latest/meta-data/')
except Exception: pass
print('RadioItem construction made outbound request to:', hits[:1])
