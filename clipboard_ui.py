import json
from html import escape

import streamlit.components.v1 as components


def render_clipboard_button(
    text: str,
    label: str,
    *,
    key: str,
) -> None:
    """Render an isolated client-side copy button without a Streamlit rerun."""
    payload = json.dumps(text, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026")
    button_id = "copy-" + "".join(character for character in key if character.isalnum() or character in "-_")
    components.html(
        f"""
        <style>
          body {{ margin: 0; font-family: system-ui, sans-serif; }}
          button {{ padding: .25rem .55rem; border: 1px solid #8886; border-radius: .4rem;
                    background: transparent; color: inherit; cursor: pointer; font-size: .8rem; }}
          span {{ margin-left: .4rem; font-size: .75rem; opacity: .7; }}
        </style>
        <button id="{button_id}" type="button">{escape(label)}</button><span id="{button_id}-status"></span>
        <script>
          const value = {payload};
          const button = document.getElementById({json.dumps(button_id)});
          const status = document.getElementById({json.dumps(button_id + '-status')});
          async function copyValue() {{
            try {{
              await navigator.clipboard.writeText(value);
            }} catch (error) {{
              const area = document.createElement('textarea');
              area.value = value;
              area.style.position = 'fixed'; area.style.opacity = '0';
              document.body.appendChild(area); area.select();
              document.execCommand('copy'); area.remove();
            }}
            status.textContent = 'Copied';
            window.setTimeout(() => status.textContent = '', 1400);
          }}
          button.addEventListener('click', copyValue);
        </script>
        """,
        height=34,
    )
