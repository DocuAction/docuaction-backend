f=open('app/bulletin_intelligence/engine.py','r')  
c=f.read()  
f.close()  
old='html = await generate_briefing_html(agency, briefing_arts, briefing_date)'  
new='try:\n    html = await generate_briefing_html(agency, briefing_arts, briefing_date)\nexcept Exception as e:\n    logger.error(f\"HTML gen failed: {e}\")\n    html=\"<h1>FCC Daily News - \"+briefing_date+\"</h1>\"'  
c=c.replace(old,new)  
f=open('app/bulletin_intelligence/engine.py','w')  
f.write(c)  
f.close()  
print('PATCHED html generation')  
