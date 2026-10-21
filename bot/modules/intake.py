"""Start a channel for a machine that just came in.

DM the bot a photo of the serial sticker (or type the serial) with the
word `intake`, and it reads the serial, looks the machine up, creates a
channel named after it (hp-z2-g8-tower), adds you, anyone you @mentioned
and the people in INTAKE_INVITE, and posts the specs card in there.

    intake                      (with a photo attached)
    intake @alice @bob          (with a photo; also add alice and bob)
    intake 9C4BPM4 @alice       (typed serial, no photo)
    intake name=brian-laptop    (choose the channel name yourself)

Needs the Slack scopes channels:manage, channels:write.invites and
users:read (in the manifest; reinstall the app after adding them).
"""
import logging
import re

from bot.config import env
from bot.module import Image, Module, Reply

log = logging.getLogger(__name__)
MENTION = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")
USER_ID = re.compile(r"^[UW][A-Z0-9]{5,}$")
NAME_OPT = re.compile(r"\bname[=:]\s*(\S+)", re.I)
SERIAL = re.compile(r"^(?=[A-Z0-9]*\d)[A-Z0-9]{5,12}$")   # must contain a digit
# Words that make channel names long without telling anything apart.
FILLER = {"desktop", "pc", "workstation", "computer", "notebook", "laptop", "mobile", "system", "the", "a"}
MAX_NAME = 80


def slug(text: str, prefix: str = "") -> str:
    """Slack channel name from a model name: lowercase, dashes, no filler.
    'HP Z2 G8 Tower Workstation Desktop PC (287S5AV)' -> 'hp-z2-g8-tower'."""
    text = re.split(r"[(\[]", text, maxsplit=1)[0]           # drop the part number in parentheses
    words = [w for w in re.sub(r"[^a-z0-9]+", " ", text.lower()).split() if w not in FILLER]
    name = "-".join(words)
    name = re.sub(r"-{2,}", "-", prefix.lower() + name).strip("-")
    return name[:MAX_NAME].rstrip("-") or "device"


class Intake(Module):
    keyword = "intake"
    aliases = ("newdevice", "channel")
    summary = "Make a channel for a device from a photo of its serial (or the serial), add people, post its specs"
    usage = "intake [@people] [name=channel-name] with a photo of the sticker, or intake <serial> [@people]"
    accepts_images = True

    def __init__(self, invite: str | None = None, prefix: str | None = None) -> None:
        self.invite_raw, self.prefix = invite, prefix

    def setup(self) -> None:
        if self.invite_raw is None:
            self.invite_raw = env("INTAKE_INVITE", "") or ""
        if self.prefix is None:
            self.prefix = (env("INTAKE_PREFIX", "") or "").strip()
        self._handles: dict[str, str] | None = None

    # ----- pieces borrowed from other modules ----------------------------
    @property
    def specs(self):
        return self.registry.modules.get("specs") if self.registry else None

    @property
    def photo(self):
        return self.registry.modules.get("photo") if self.registry else None

    @property
    def slack(self):
        return self.registry.slack if self.registry else None

    # ----- the pieces ----------------------------------------------------
    def read_serials(self, images: list[Image]) -> list[str]:
        photo = self.photo
        if photo is None:
            return []
        found: list[str] = []
        for image in images:
            serials, _ = photo.read(image)
            found += [s for s in serials if s not in found]
        specs = self.specs
        if specs:
            found = [s for s in found if any(v.looks_like(s) for v in specs.vendors)] or found
        return found

    def resolve_people(self, text: str, requester: str) -> list[str]:
        """Requester + @mentions in the message + INTAKE_INVITE, as user IDs."""
        people = [requester] + MENTION.findall(text)
        for raw in re.split(r"[\s,]+", self.invite_raw or ""):
            raw = raw.strip().lstrip("@")
            if not raw:
                continue
            m = MENTION.fullmatch(raw)
            if m:
                people.append(m.group(1))
            elif USER_ID.match(raw.upper()):
                people.append(raw.upper())
            else:
                uid = self.handle_to_id(raw)
                if uid:
                    people.append(uid)
                else:
                    log.warning("intake: no Slack user with handle @%s", raw)
        out: list[str] = []
        for p in people:
            if p and p not in out:
                out.append(p)
        return out

    def handle_to_id(self, handle: str) -> str:
        if self._handles is None:
            self._handles = {}
            try:
                cursor = None
                while True:
                    page = self.slack.users_list(limit=200, cursor=cursor)
                    for u in page.get("members", []):
                        for key in (u.get("name"), (u.get("profile") or {}).get("display_name")):
                            if key:
                                self._handles[key.lower()] = u["id"]
                    cursor = (page.get("response_metadata") or {}).get("next_cursor")
                    if not cursor:
                        break
            except Exception as exc:
                log.warning("intake: users.list failed (users:read scope?): %s", exc)
        return self._handles.get(handle.lower(), "")

    def create_channel(self, name: str, serial: str) -> tuple[str, str]:
        """(channel id, final name). On a name clash the serial's tail is appended."""
        tries = [name, f"{name}-{serial[-4:].lower()}", f"{name}-{serial.lower()}"]
        last = None
        for candidate in tries:
            try:
                r = self.slack.conversations_create(name=candidate, is_private=False)
                return r["channel"]["id"], r["channel"]["name"]
            except Exception as exc:
                last = exc
                if "name_taken" not in str(exc):
                    raise
        raise last

    # ----- entry points --------------------------------------------------
    def handle(self, args: str, user_id: str) -> Reply:
        words = [w for w in args.split() if not MENTION.fullmatch(w) and not NAME_OPT.fullmatch(w)]
        serial = re.sub(r"[^A-Z0-9]", "", words[0].upper()) if words else ""
        if not serial or not SERIAL.match(serial):
            return Reply("Attach a photo of the serial sticker with `intake`, or type `intake <serial> [@people]`.")
        return self.start(serial, args, user_id)

    def handle_images(self, images: list[Image], text: str, user_id: str) -> Reply:
        words = [w for w in text.split() if not MENTION.fullmatch(w) and not NAME_OPT.fullmatch(w)]
        typed = [re.sub(r"[^A-Z0-9]", "", w.upper()) for w in words]
        typed = [t for t in typed if SERIAL.match(t)]
        if typed:
            return self.start(typed[0], text, user_id)
        try:
            serials = self.read_serials(images)
        except Exception as exc:
            log.warning("intake: reading the photo failed: %r", exc)
            return Reply(":warning: I couldn't get the vision model to read that photo. Type the serial: `intake <serial>`.")
        if not serials:
            return Reply(":mag: I couldn't make out a serial in that photo. Try a closer shot, or type `intake <serial>`.")
        return self.start(serials[0], text, user_id)

    def start(self, serial: str, text: str, user_id: str) -> Reply:
        if self.slack is None:
            return Reply(":warning: No Slack connection to create channels with.")
        specs = self.specs
        card = specs.handle(f"specs {serial}", user_id) if specs else Reply(f"`{serial}`")
        looked_up = bool(card.blocks) and not card.text.startswith((":warning:", ":no_entry:", "No match"))
        title = card.text.split("\n", 1)[0].strip("*").split("* (")[0].strip("*") if looked_up else serial
        m = NAME_OPT.search(text)
        name = slug(m.group(1) if m else title, self.prefix)

        try:
            channel, name = self.create_channel(name, serial)
        except Exception as exc:
            err = str(exc)
            if "missing_scope" in err:
                return Reply(":warning: I can't create channels yet: the Slack app needs the `channels:manage` and "
                             "`channels:write.invites` scopes. Update the manifest from the repo and click Reinstall.")
            log.warning("intake: conversations.create failed: %s", err)
            return Reply(f":warning: Couldn't create the channel `{name}`: `{err[:200]}`")

        people = self.resolve_people(text, user_id)
        not_added: list[str] = []
        try:
            self.slack.conversations_invite(channel=channel, users=",".join(people))
        except Exception as exc:
            err = str(exc)
            log.warning("intake: conversations.invite failed: %s", err)
            if "already_in_channel" not in err:
                not_added = people
        try:
            self.slack.conversations_setTopic(channel=channel, topic=f"{title} | {serial}"[:250])
        except Exception as exc:
            log.info("intake: setTopic failed: %s", exc)
        try:
            self.slack.chat_postMessage(channel=channel, text=card.text, blocks=(card.blocks or None),
                                        unfurl_links=False, unfurl_media=False)
        except Exception as exc:
            log.warning("intake: posting specs failed: %s", exc)
            not_added.append("(specs post failed)")

        lines = [f":white_check_mark: Made <#{channel}> for *{title}* (`{serial}`) and posted its specs there."]
        others = [p for p in people if p != user_id]
        if others:
            lines.append("Added " + ", ".join(f"<@{p}>" for p in others) + ".")
        if not looked_up:
            lines.append("The lookup didn't return specs, so the channel is named after the serial.")
        if not_added:
            lines.append(":warning: Couldn't add everyone (" + ", ".join(f"<@{p}>" if p.startswith(("U", "W")) else p for p in not_added) + "); check the container logs.")
        return Reply("\n".join(lines))
