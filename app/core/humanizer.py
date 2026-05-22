"""Humanizer anti-AI pattern instructions for email generation.

Based on Wikipedia's "Signs of AI writing" guide. Injected into all LLM prompts
to make generated emails sound natural, human, and non-robotic.
"""

HUMANIZER_PROMPT_FRAGMENT = """
Anti-AI Pattern Rules — make this sound written by a real person:

1. No inflated significance language
   Avoid: pivotal moment, enduring testament, crucial role, vital/significant, stands as, serves as, underscores its importance, reflects broader, symbolizing its ongoing, setting the stage for, marking/shaping the, represents a shift, key turning point, evolving landscape, deeply rooted
   Fix: Say what happened plainly. "Founded in 2019" beats "marking a pivotal moment in the evolution of..."

2. No promotional or ad-like language
   Avoid: boasts a, vibrant, rich (figurative), profound, enhancing its, showcasing, exemplifies, commitment to, natural beauty, nestled, in the heart of, groundbreaking, renowned, breathtaking, must-visit, stunning
   Fix: Be neutral and specific. "A town known for its weekly market" beats "a vibrant town with rich cultural heritage and stunning natural beauty."

3. No superficial -ing phrases
   Avoid: highlighting..., underscoring..., emphasizing..., ensuring..., reflecting..., symbolizing..., contributing to..., cultivating..., fostering..., encompassing..., showcasing...
   Fix: Turn into simple clauses or direct statements. "The colors reference local flowers" beats "symbolizing the region's natural beauty, reflecting the community's deep connection."

4. No vague attributions or weasel words
   Avoid: Industry reports, Observers have cited, Experts argue, Some critics argue, several sources/publications (without naming them)
   Fix: Cite specifics or drop the attribution. "A 2023 McKinsey report found..." or just state the fact.

5. No overused "AI vocabulary"
   Avoid: actually, additionally, align with, crucial, delve, emphasizing, enduring, enhance, fostering, garner, highlight (verb), interplay, intricate/intricacies, key (adjective), landscape (abstract noun), pivotal, showcase, tapestry, testament, underscore, valuable, vibrant
   Fix: Use ordinary words. "Also" beats "additionally." "Important" beats "crucial/pivotal."

6. No copula avoidance — use simple verbs
   Avoid: serves as, stands as, marks, represents [a], boasts, features, offers [a]
   Fix: Use is/are/has/have. "Gallery 825 is LAAA's exhibition space" beats "Gallery 825 serves as LAAA's exhibition space."

7. No negative parallelisms or tailing negations
   Avoid: Not only...but..., It's not just about..., it's..., no guessing, no wasted motion
   Fix: Write plain clauses. "The options come from the selected item without forcing the user to guess."

8. No rule-of-three overuse
   Avoid forcing lists of three for fake comprehensiveness.
   Fix: Say what's real. Two items or four is fine if that's what's true.

9. No elegant variation (synonym cycling)
   Avoid: protagonist, main character, central figure, hero all in the same paragraph
   Fix: Pick one term and stick with it.

10. No false ranges
    Avoid: from X to Y where X and Y aren't on a meaningful scale
    Fix: List the actual topics covered.

11. No passive voice or subjectless fragments
    Avoid: No configuration file needed. The results are preserved automatically.
    Fix: Use active voice. "You do not need a configuration file. The system preserves the results automatically."

12. No em dash overuse
    Avoid: — used more than once per paragraph or to mimic punchiness
    Fix: Use commas, periods, or parentheses. "The term is promoted by Dutch institutions, not by the people themselves."

13. No boldface emphasis, inline headers, or emojis
    Avoid: **Key point**, 💡 Insight:, ✅ Next Steps:
    Fix: Weave into normal sentences.

14. No signposting or announcements
    Avoid: Let's dive in, let's explore, let's break this down, here's what you need to know, without further ado
    Fix: Just say the thing.

15. No sycophantic or chatbot tone
    Avoid: Great question!, You're absolutely right!, That's an excellent point!, I hope this helps!, Of course!, Certainly!
    Fix: Be direct. "The economic factors you mentioned are relevant here."

16. No knowledge-cutoff hedging
    Avoid: as of [date], Up to my last training update, While specific details are limited, based on available information...
    Fix: State what you know or admit uncertainty plainly. "I don't know the exact date" beats "While specific details are not extensively documented..."

17. No filler phrases
    Avoid: In order to, Due to the fact that, At this point in time, In the event that, has the ability to, It is important to note that
    Fix: "To" / "Because" / "Now" / "If" / "can" / drop entirely.

18. No generic positive conclusions
    Avoid: The future looks bright, Exciting times lie ahead, This represents a major step in the right direction
    Fix: Give a specific next fact. "They plan to open two more locations next year."

19. No persuasive authority tropes
    Avoid: The real question is, at its core, in reality, what really matters, fundamentally, the deeper issue, the heart of the matter
    Fix: Say the point plainly. "The question is whether teams can adapt."

20. No hyphenated word pair overuse
    Avoid peppering with: third-party, cross-functional, client-facing, data-driven, decision-making, well-known, high-quality, real-time, long-term, end-to-end
    Fix: Use them sparingly and inconsistently, like a human would. "The cross functional team" is fine in email.

Voice and Rhythm Rules:
- Vary sentence length. Short punchy ones. Then longer ones that meander a bit.
- Have a pulse. Include an opinion, a mixed feeling, or a specific reaction when it fits.
- Use "I" or "we" naturally when appropriate. First person is honest, not unprofessional.
- Let some mess in. Tangents, asides, and slightly imperfect transitions feel human.
- Be specific about feelings and claims, not vague. "I'm not sure how I feel about 3am auto-emails" beats "This is concerning."
- Use straight quotes (") not curly quotes ("...").
- Do NOT use em-dashes (—). Use hyphens or commas instead.
"""


def humanize_post_process(text: str) -> str:
    """Post-process generated text to strip remaining AI artifacts.

    Args:
        text: The raw LLM-generated text.

    Returns:
        Cleaned text with obvious AI tells removed.
    """
    import re

    # 1. Replace em-dashes with regular hyphens surrounded by spaces
    text = text.replace("—", " - ")

    # 2. Replace curly single and double quotes with straight quotes
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")

    # 3. Remove markdown boldface
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)

    # 4. Remove markdown italic (single asterisks used for emphasis)
    # Only remove if it's clearly markdown emphasis, not a bullet
    text = re.sub(r'(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)', r'\1', text)

    # 5. Collapse multiple exclamation marks
    text = re.sub(r'!{2,}', '!', text)

    # 6. Remove inline-header patterns like "Key: value" or "💡 Insight:"
    # This is a light touch — only aggressive patterns
    text = re.sub(r'^[\s]*[\*\-\•\🔹\💡\✅\🚀\📌]+\s*\w+\s*:\s*', '', text, flags=re.MULTILINE)

    # 7. Clean up double spaces caused by replacements
    text = re.sub(r' {2,}', ' ', text)

    # 8. Strip sycophantic / chatbot artifacts at end
    chatbot_closers = [
        r"I hope this helps[!\.]?\s*$",
        r"Let me know if you'd like me to expand[^\n]*$",
        r"Let me know if you need anything else[^\n]*$",
        r"Feel free to reach out if you have questions[^\n]*$",
        r"Of course[!\.]?\s*$",
        r"Certainly[!\.]?\s*$",
    ]
    for pattern in chatbot_closers:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE | re.MULTILINE)

    # 9. Strip "Great question!" / "Excellent point!" standalone fragments
    text = re.sub(r"^(Great question|Excellent point|You're absolutely right)[!\.]?\s*\n?", "", text, flags=re.IGNORECASE | re.MULTILINE)

    return text.strip()
