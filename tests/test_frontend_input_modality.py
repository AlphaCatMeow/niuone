import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INPUT_MODALITY = ROOT / 'web' / 'src' / 'utils' / 'inputModality.js'
MAIN_SOURCE = ROOT / 'web' / 'src' / 'main.js'
GLOBAL_STYLES = ROOT / 'web' / 'src' / 'styles.css'


class FrontendInputModalityTests(unittest.TestCase):
    def test_pointer_focus_ring_is_suppressed_without_hiding_keyboard_focus(self):
        main_source = MAIN_SOURCE.read_text(encoding='utf-8')
        styles = GLOBAL_STYLES.read_text(encoding='utf-8')

        self.assertIn("installInputModalityTracker()", main_source)
        self.assertIn("html[data-input-modality='pointer'] :where(", styles)
        self.assertIn("html[data-input-modality='pointer'] .market-breadth-toggle:focus-within", styles)
        self.assertNotIn("html[data-input-modality='keyboard']", styles)

    def test_tracker_switches_between_pointer_and_keyboard_input(self):
        scenario = f"""
import {{ installInputModalityTracker }} from {json.dumps(INPUT_MODALITY.as_uri())};

const listeners = new Map();
const attributes = {{}};
const documentRef = {{
  documentElement: {{
    setAttribute(name, value) {{ attributes[name] = value; }},
  }},
  addEventListener(name, handler, capture) {{ listeners.set(name, {{ handler, capture }}); }},
  removeEventListener(name, handler, capture) {{
    const current = listeners.get(name);
    if (current?.handler === handler && current.capture === capture) listeners.delete(name);
  }},
}};

const dispose = installInputModalityTracker(documentRef);
listeners.get('pointerdown').handler({{}});
const pointer = attributes['data-input-modality'];
listeners.get('keydown').handler({{ altKey: false, ctrlKey: false, metaKey: false }});
const keyboard = attributes['data-input-modality'];
listeners.get('pointerdown').handler({{}});
listeners.get('keydown').handler({{ altKey: false, ctrlKey: true, metaKey: false }});
const modifiedKey = attributes['data-input-modality'];
const capture = [...listeners.values()].every((listener) => listener.capture === true);
dispose();
console.log(JSON.stringify({{ pointer, keyboard, modifiedKey, capture, remaining: listeners.size }}));
"""
        output = subprocess.check_output(
            ['node', '--input-type=module', '-e', scenario],
            cwd=ROOT,
            text=True,
        )

        self.assertEqual(
            json.loads(output),
            {
                'pointer': 'pointer',
                'keyboard': 'keyboard',
                'modifiedKey': 'pointer',
                'capture': True,
                'remaining': 0,
            },
        )


if __name__ == '__main__':
    unittest.main()
