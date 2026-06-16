import ast  
f=open('app/bulletin_intelligence/engine.py','r')  
c=f.read()  
f.close()  
old='classified = await classify_articles(unique, agency)'  
