import json
from app.config import settings
def _mock_plan(goal):
    return {"reply":f"Thanks for sharing your goal: {goal}. What business emails do you write most often, and what is your biggest challenge—tone, clarity, grammar, or structure?","ready":False,"goal_summary":goal.strip(),"tasks":[]}
def _mock_task(goal):
    rubric={"criteria":[{"name":"Purpose and completeness","points":30},{"name":"Professional tone","points":25},{"name":"Clarity and organization","points":25},{"name":"Grammar and vocabulary","points":20}],"pass_threshold":settings.pass_threshold}
    return {"reply":"Great—let's begin with a practical business-email task. Submit a complete draft, and I'll assess it against the rubric.","ready":True,"goal_summary":goal.strip(),"tasks":[{"title":"Write a polite project-delay email","prompt":"Write a 120–180 word email to a client explaining that a project delivery will be delayed by three business days. Apologize briefly, explain the next step, and maintain a professional, reassuring tone.","rubric":json.dumps(rubric)}]}
def coach_message(history, user_message, mode="模拟 AI"):
    if mode!="真实 AI" or not settings.openai_api_key:
        user_turns=sum(1 for m in history if m.get("role")=="user")
        return _mock_plan(user_message) if user_turns==0 else _mock_task(user_message)
    from openai import OpenAI
    client=OpenAI(api_key=settings.openai_api_key)
    system=("You are an email-writing coach. Reply in the learner's discussion language; assignment prompts in English. "
    "Ask clarifying questions before assigning work. Return JSON only: reply (string), ready (boolean), "
    "goal_summary (string), tasks (array of objects with title, prompt, rubric). If enough context, ready=true and include one task. "
    "Rubric must be a JSON string with criteria and pass_threshold.")
    messages=[{"role":"system","content":system}]
    messages.extend({"role":m["role"],"content":m["content"]} for m in history[-10:] if m.get("role") in {"user","assistant"})
    messages.append({"role":"user","content":user_message})
    r=client.chat.completions.create(model=settings.openai_model,messages=messages,response_format={"type":"json_object"},temperature=0.4)
    data=json.loads(r.choices[0].message.content or "{}")
    data.setdefault("reply","Could you tell me a little more about your goals?")
    data.setdefault("ready",False); data.setdefault("goal_summary",user_message); data.setdefault("tasks",[])
    return data
