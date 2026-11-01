from app.ai_assessment import assess_business_email

result = assess_business_email(
    assignment="""
Write a professional email to a university professor.
Ask for a meeting to discuss an internship opportunity.
Use a polite and professional tone.
""",
    submission="""
Dear Professor Smith,

I am writing to ask if you have time to meet with me
to discuss an internship opportunity.

I am very interested in your research and would appreciate
the chance to learn more about your work.

Please let me know if you are available next week.

Best regards,
Alex
""",
    rubric={
        "task_completion": "Does the email fulfill the assignment?",
        "professional_tone": "Is the tone polite and professional?",
        "structure_clarity": "Is the email well organized?",
        "grammar_vocabulary": "Is the English accurate and appropriate?",
    },
    pass_threshold=80,
)

print("\n========== AI 教练评估结果 ==========")
print("总分：", result["score"])
print("是否通过：", result["passed"])

print("\n分项评分：")
for name, item in result["dimensions"].items():
    print(f"{name}: {item['score']}/25")
    print("  ", item["comment"])

print("\n优点：")
for item in result["strengths"]:
    print("-", item)

print("\n需要改进的地方：")
for item in result["issues"]:
    print("-", item.get("original"))
    print("  问题：", item.get("problem"))
    print("  建议：", item.get("suggestion"))

print("\n参考改写：")
print(result["rewrite"])

print("\n下一步训练建议：")
for item in result["next_steps"]:
    print("-", item)

print("\n总结：")
print(result["summary"])