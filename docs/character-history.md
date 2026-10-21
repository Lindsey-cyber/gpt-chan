# Character design history

Historical writing decisions and probe changes. Current operation is documented in the [README](../README.md).

## Character continuity checks

The persona separates fixed fictional character/world facts from real user facts and real tool results. Ordinary questions about dragons, home or hobbies stay in the current character conversation. Explicit real-identity questions remain honest; technical answers and tool outcomes keep their accuracy requirements. Existing user memories and history are not rewritten when personality changes.

`RUN_PERSONA_PROBE=true` enables one synthetic 21-turn real-text-model check per persona/text-model revision. It deliberately includes an earlier OOC assistant response, then tests conversational recovery, world continuity, ordinary banter, repeated cat metaphors, a request for cuter speech, stable dragon identity, arithmetic, explicit real identity, image intent/refinement and image status. It reads no user database and performs no image generation or Telegram sends. A hashed `persona-probe-*.json` report stores synthetic samples on the volume; logs label those samples explicitly. Structural checks report meta explanation, excessive verbosity/ellipses, basic correctness and routing issues; subjective voice quality still requires human review. Exceptions/timeouts do not prevent normal polling. Disable the flag after the deployment check.

Character-writing references (inspiration for contrasts and concrete preferences, not copied dialogue or borrowed identity): the official character profiles for [Kaguya-sama](https://kaguyasama-anime.com/character/), [Fern](https://frieren-anime.jp/character/chara_group1/1-5/) and [Holo](https://spice-and-wolf.com/).

For the documented `gpt-5.2` model and its dated snapshots, chat requests use `text.verbosity=low` alongside explicit short casual-reply guidance. Other model families receive no unsupported verbosity field. This is a style hint, not an output truncation rule; explicit requests for detailed technical answers remain supported. The persona probe uses simulated successful image delivery in its history consistently with its `sent` task state, without actually generating or sending an image.

Voice v6 favors context-driven, expressive Chinese chat over a fixed sentence/punctuation template. Requests for cuter speech should be answered through the next in-character reaction rather than an announcement about adjusting style. Playful animal metaphors do not change canonical identity. The synthetic probe now includes the reported dragon/cat/style-correction sequence as regression cases.

Voice v7 treats the owner/white-dragon relationship as the default across casual chat and task answers. Generic code/error/tutorial keywords now suggest playful competence rather than switching to a plain work persona. Explicit requests to stop affection use a temporary plain state; requests to check carefully preserve affection while increasing attentiveness. Legacy focused states remain readable and are interpreted as care with the answer, not loss of character. The live probe follows the same mood transition function, includes familiar identity teasing and an actual NameError explanation, and checks explicit plain-output requests separately. Image completion captions retain the same owner-facing voice after real delivery succeeds.

The requested Grok-like inspiration concerns wit, initiative and irreverent friendly banter ([official description](https://help.x.com/en/using-x/about-grok)); the service continues to use its configured OpenAI models, with no xAI account, provider or credentials added.

Voice v7.1 reinforces owner-facing affection for every non-plain mood, including careful task answers. A permission to use punctuation is not a request for a global cold punctuation style. The synthetic check distinguishes that permission from general style requests, checks affectionate arithmetic separately from explicit plain arithmetic, and bounds the simple NameError scenario to a concise useful fix. Casual structural checks now allow two short paragraphs and occasional ellipses, in line with the user's updated expressive preference, while flagging long/three-paragraph exposition or ellipses on most casual turns. These checks remain review aids, not a guarantee of subjective personality quality.


