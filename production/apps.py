from django.apps import AppConfig
import re


class ProductionConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'production'

    def ready(self):
        import django.template.base as dtb
        orig_init = dtb.Template.__init__

        def _clean_var(m):
            return "{{" + " ".join(m.group(1).split()) + "}}"

        def _clean_tag(m):
            raw = m.group(1)
            raw = re.sub(r'\s*(==|!=|<=|>=|<|>)\s*', r' \1 ', raw)
            return "{% " + " ".join(raw.split()) + " %}"

        def patched_init(self, template_string, origin=None, name=None, engine=None):
            if isinstance(template_string, str):
                template_string = re.sub(r"\{\{([\s\S]*?)\}\}", _clean_var, template_string)
                template_string = re.sub(r"\{%([\s\S]*?)%\}", _clean_tag, template_string)
            orig_init(self, template_string, origin, name, engine)

        dtb.Template.__init__ = patched_init
