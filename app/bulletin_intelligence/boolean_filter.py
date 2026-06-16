import re
from typing import List,Tuple
FCC_SECTIONS=["fcc_news","consumers","media_broadcasting","space_policy","public_safety","wireless_spectrum","ai_ml","business_tech","international"]
SECTION_KEYWORDS={"fcc_news":["fcc","federal communications commission","brendan carr","olivia trusty","anna gomez","fcc chairman"],"consumers":["tcpa","robocall","spoofing","phone scam","stir-shaken","caller id","cramming","burner phone"],"media_broadcasting":["broadcast license","radio station","cable tv","satellite tv","media ownership","fm radio"],"space_policy":["satellite","starlink","spacex","space","orbital","nasa","submarine cable","undersea cable"],"public_safety":["911","emergency alert","cybersecurity","data breach","privacy","public safety"],"wireless_spectrum":["spectrum","broadband","wireless","5g","cell tower","telecom","cbrs","auction","fiber","docsis"],"ai_ml":["artificial intelligence","machine learning","generative ai","ai governance","ai regulation"],"business_tech":["net neutrality","internet policy","telecom industry","ipo","trillionaire"],"international":["undersea cable","subsea cable","itu","china telecom","china unicom"]}
def assign_section(title,summary):
 text=f"{title} {summary}".lower()
 hits=[s for s,kws in SECTION_KEYWORDS.items() if any(k in text for k in kws)]
 if not hits:return "other",[]
 for s in ["space_policy","public_safety","ai_ml","international","consumers","media_broadcasting","wireless_spectrum","business_tech","fcc_news"]:
  if s in hits:return s,hits
 return hits[0],hits
def is_fcc_relevant(t,s):return assign_section(t,s)[0]!="other"
