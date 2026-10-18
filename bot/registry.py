import importlib
import inspect
import logging
import pkgutil

from bot import modules as modules_pkg
from bot.module import Module, Reply

log = logging.getLogger(__name__)


class Registry:
    def __init__(self) -> None:
        self.modules: dict[str, Module] = {}
        self._lookup: dict[str, Module] = {}

    def add(self, module: Module) -> None:
        self.modules[module.keyword] = module
        for word in (module.keyword, *module.aliases):
            self._lookup[word.lower()] = module

    def discover(self, enabled: set[str] | None = None) -> None:
        """Import every file in bot/modules and register its Module subclasses."""
        for info in pkgutil.iter_modules(modules_pkg.__path__):
            mod = importlib.import_module(f"{modules_pkg.__name__}.{info.name}")
            for _, cls in inspect.getmembers(mod, inspect.isclass):
                if not issubclass(cls, Module) or cls is Module or not cls.keyword:
                    continue
                if cls.__module__ != mod.__name__:
                    continue
                if enabled is not None and cls.keyword not in enabled and cls.keyword != "help":
                    continue
                instance = cls(self) if cls.keyword == "help" else cls()
                try:
                    instance.setup()
                except (Exception, SystemExit) as exc:
                    log.warning("Module %s disabled: %s", cls.keyword, exc)
                    continue
                self.add(instance)
                log.info("Loaded module %s", cls.keyword)

    def dispatch(self, text: str, user_id: str) -> Reply:
        text = (text or "").strip()
        keyword, _, args = text.partition(" ")
        module = self._lookup.get(keyword.lower()) if keyword else None
        if module is None:
            # No keyword: maybe a module recognises the content itself (a pasted file).
            module = next((m for m in self.modules.values() if m.matches(text)), None)
            if module is not None:
                args = text
            else:
                module = self._lookup["help"]
                args = ""
        elif module.pass_keyword:
            args = f"{keyword.lower()} {args}"
        try:
            return module.handle(args.strip(), user_id)
        except Exception:
            log.exception("Module %s failed", module.keyword)
            return Reply(f":warning: `{module.keyword}` hit an error. Check the container logs.")
