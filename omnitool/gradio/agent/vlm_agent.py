import json
from collections.abc import Callable
from typing import cast, Callable
import uuid
from PIL import Image, ImageDraw
import base64
from io import BytesIO

from anthropic import APIResponse
from anthropic.types import ToolResultBlockParam
from anthropic.types.beta import BetaMessage, BetaTextBlock, BetaToolUseBlock, BetaMessageParam, BetaUsage

from agent.llm_utils.oaiclient import run_oai_interleaved
from agent.llm_utils.groqclient import run_groq_interleaved
from agent.llm_utils.geminiclient import run_gemini_interleaved
from agent.llm_utils.utils import is_image_path
import time
import re

OUTPUT_DIR = "./tmp/outputs"

def extract_data(input_string, data_type):
    """
    提取代码块中的内容，支持多种格式
    """
    if not input_string or not isinstance(input_string, str):
        return ""
    
    # 尝试多种模式来提取JSON内容
    patterns = [
        # 标准的代码块格式
        f"```{data_type}\\s*(.*?)\\s*```",
        # 没有语言标识的代码块
        f"```\\s*({{.*?}})\\s*```",
        # 仅有开始标记没有结束标记
        f"```{data_type}\\s*(.*?)$",
        # 直接的JSON内容（以大括号开始和结束）
        r"(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})",
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, input_string, re.DOTALL | re.IGNORECASE)
        if matches:
            # 返回第一个匹配，去除前后空白
            content = matches[0]
            if isinstance(content, tuple):
                content = content[0]
            return content.strip()
    
    # 如果没有找到代码块，尝试查找JSON对象
    # 查找第一个完整的JSON对象
    json_start = input_string.find('{')
    if json_start != -1:
        brace_count = 0
        for i, char in enumerate(input_string[json_start:], json_start):
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    return input_string[json_start:i+1].strip()
    
    # 如果都没找到，返回原字符串
    return input_string.strip()

class VLMAgent:
    def __init__(
        self,
        model: str, 
        provider: str, 
        api_key: str,
        output_callback: Callable, 
        api_response_callback: Callable,
        max_tokens: int = 4096,
        only_n_most_recent_images: int | None = None,
        print_usage: bool = True,
    ):
        if model == "omniparser + gpt-4o":
            self.model = "gpt-4o-2024-11-20"
        elif model == "omniparser + R1":
            self.model = "deepseek-r1-distill-llama-70b"
        elif model == "omniparser + qwen2.5vl":
            self.model = "qwen2.5-vl-72b-instruct"
        elif model == "omniparser + qwen2.5vl-local":
            self.model = "qwen2.5vl:3b"  # 修正为实际的 Ollama 模型名称（无连字符）
        elif model == "omniparser + o1":
            self.model = "o1"
        elif model == "omniparser + o3-mini":
            self.model = "o3-mini"
        elif model == "omniparser + gemini-2.5-flash":
            self.model = "gemini-2.5-flash"
        else:
            raise ValueError(f"Model {model} not supported")
        

        self.provider = provider
        self.api_key = api_key
        self.api_response_callback = api_response_callback
        self.max_tokens = max_tokens
        self.only_n_most_recent_images = only_n_most_recent_images
        self.output_callback = output_callback

        self.print_usage = print_usage
        self.total_token_usage = 0
        self.total_cost = 0
        self.step_count = 0

        self.system = ''
           
    def __call__(self, messages: list, parsed_screen: list[str, list, dict]):
        self.step_count += 1
        image_base64 = parsed_screen['original_screenshot_base64']
        latency_omniparser = parsed_screen['latency']
        self.output_callback(f'-- Step {self.step_count}: --', sender="bot")
        screen_info = str(parsed_screen['screen_info'])
        screenshot_uuid = parsed_screen['screenshot_uuid']
        screen_width, screen_height = parsed_screen['width'], parsed_screen['height']

        boxids_and_labels = parsed_screen["screen_info"]
        system = self._get_system_prompt(boxids_and_labels)

        # drop looping actions msg, byte image etc
        planner_messages = messages
        _remove_som_images(planner_messages)
        _maybe_filter_to_n_most_recent_images(planner_messages, self.only_n_most_recent_images)

        if isinstance(planner_messages[-1], dict):
            if not isinstance(planner_messages[-1]["content"], list):
                planner_messages[-1]["content"] = [planner_messages[-1]["content"]]
            planner_messages[-1]["content"].append(f"{OUTPUT_DIR}/screenshot_{screenshot_uuid}.png")
            planner_messages[-1]["content"].append(f"{OUTPUT_DIR}/screenshot_som_{screenshot_uuid}.png")

        start = time.time()
        if "gpt" in self.model or "o1" in self.model or "o3-mini" in self.model:
            vlm_response, token_usage = run_oai_interleaved(
                messages=planner_messages,
                system=system,
                model_name=self.model,
                api_key=self.api_key,
                max_tokens=self.max_tokens,
                provider_base_url="https://api.openai.com/v1",
                temperature=0,
            )
            print(f"oai token usage: {token_usage}")
            self.total_token_usage += token_usage
            if 'gpt' in self.model:
                self.total_cost += (token_usage * 2.5 / 1000000)  # https://openai.com/api/pricing/
            elif 'o1' in self.model:
                self.total_cost += (token_usage * 15 / 1000000)  # https://openai.com/api/pricing/
            elif 'o3-mini' in self.model:
                self.total_cost += (token_usage * 1.1 / 1000000)  # https://openai.com/api/pricing/
        elif "r1" in self.model:
            vlm_response, token_usage = run_groq_interleaved(
                messages=planner_messages,
                system=system,
                model_name=self.model,
                api_key=self.api_key,
                max_tokens=self.max_tokens,
            )
            print(f"groq token usage: {token_usage}")
            self.total_token_usage += token_usage
            self.total_cost += (token_usage * 0.99 / 1000000)
        elif "qwen" in self.model and self.provider != "local":
            vlm_response, token_usage = run_oai_interleaved(
                messages=planner_messages,
                system=system,
                model_name=self.model,
                api_key=self.api_key,
                max_tokens=min(2048, self.max_tokens),
                provider_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                temperature=0,
            )
            print(f"qwen token usage: {token_usage}")
            self.total_token_usage += token_usage
            self.total_cost += (token_usage * 2.2 / 1000000)  # https://help.aliyun.com/zh/model-studio/getting-started/models?spm=a2c4g.11186623.0.0.74b04823CGnPv7#fe96cfb1a422a
        elif "gemini" in self.model:
            # Gemini 模型处理
            vlm_response, token_usage = run_gemini_interleaved(
                messages=planner_messages,
                system=system,
                model_name=self.model,
                api_key=self.api_key,
                max_tokens=min(2048, self.max_tokens),
                temperature=0,
            )
            print(f"gemini token usage: {token_usage}")
            self.total_token_usage += token_usage
            # Gemini-2.5-flash定价约为$0.5/1M tokens
            self.total_cost += (token_usage * 0.5 / 1000000)
        elif self.provider == "local":
            # Local Ollama deployment
            vlm_response, token_usage = run_oai_interleaved(
                messages=planner_messages,
                system=system,
                model_name=self.model,
                api_key="dummy",  # Ollama doesn't need API key
                max_tokens=min(2048, self.max_tokens),
                provider_base_url="http://localhost:11434/v1",
                temperature=0,
            )
            print(f"local model token usage: {token_usage}")
            self.total_token_usage += token_usage
            # No cost for local deployment
        else:
            raise ValueError(f"Model {self.model} not supported")
        latency_vlm = time.time() - start
        self.output_callback(f"LLM: {latency_vlm:.2f}s, OmniParser: {latency_omniparser:.2f}s", sender="bot")

        print(f"{vlm_response}")
        
        if self.print_usage:
            print(f"Total token so far: {self.total_token_usage}. Total cost so far: $USD{self.total_cost:.5f}")
        
        print(f"🔍 Raw VLM Response:\n{vlm_response}")
        print("="*50)
        
        vlm_response_json = extract_data(vlm_response, "json")
        print(f"📝 Extracted JSON content:\n{vlm_response_json}")
        print("="*50)
        
        # Clean up common JSON formatting issues
        vlm_response_json = vlm_response_json.strip()
        
        # Remove markdown code block markers if present
        if vlm_response_json.startswith('```json'):
            vlm_response_json = vlm_response_json[7:]
        if vlm_response_json.startswith('```'):
            vlm_response_json = vlm_response_json[3:]
        if vlm_response_json.endswith('```'):
            vlm_response_json = vlm_response_json[:-3]
        
        vlm_response_json = vlm_response_json.strip()
        
        # Remove trailing commas before closing braces/brackets
        import re
        vlm_response_json = re.sub(r',(\s*[}\]])', r'\1', vlm_response_json)
        
        # If the response is empty or just whitespace, create a default response
        if not vlm_response_json or vlm_response_json.isspace():
            print("⚠️  Empty JSON content detected, using default response")
            vlm_response_json = {
                "Reasoning": "Empty response received, taking screenshot to assess current state",
                "Next Action": "screenshot"
            }
        else:
            try:
                vlm_response_json = json.loads(vlm_response_json)
                print("✅ JSON parsing successful")
            except json.JSONDecodeError as e:
                print(f"❌ JSON parsing error: {e}")
                print(f"🔍 Problematic JSON content: '{vlm_response_json}'")
                print(f"📏 Content length: {len(vlm_response_json)}")
                
                # Try to fix common issues and parse again
                try:
                    # Remove any trailing commas after the last property
                    cleaned_json = re.sub(r',(\s*)(?=})', r'\1', vlm_response_json)
                    # Remove any leading/trailing non-JSON characters
                    cleaned_json = re.sub(r'^[^{]*({.*})[^}]*$', r'\1', cleaned_json, flags=re.DOTALL)
                    vlm_response_json = json.loads(cleaned_json)
                    print("✅ JSON parsing successful after cleanup")
                except json.JSONDecodeError as e2:
                    print(f"❌ Failed to parse JSON even after cleanup: {e2}")
                    print(f"🔍 Final cleaned content: '{cleaned_json if 'cleaned_json' in locals() else vlm_response_json}'")
                    
                    # Analyze the content to provide better error information
                    if len(vlm_response_json) == 0:
                        error_reason = "Empty response"
                    elif not vlm_response_json.strip().startswith('{'):
                        error_reason = "Response doesn't start with '{'"
                    elif not vlm_response_json.strip().endswith('}'):
                        error_reason = "Response doesn't end with '}'"
                    else:
                        error_reason = "Invalid JSON syntax"
                    
                    print(f"🎯 Error analysis: {error_reason}")
                    
                    # Create a default response to continue execution
                    vlm_response_json = {
                        "Reasoning": f"JSON parsing failed ({error_reason}), taking screenshot to assess current state",
                        "Next Action": "screenshot"
                    }

        img_to_show_base64 = parsed_screen["som_image_base64"]
        if "Box ID" in vlm_response_json:
            try:
                bbox = parsed_screen["parsed_content_list"][int(vlm_response_json["Box ID"])]["bbox"]
                vlm_response_json["box_centroid_coordinate"] = [int((bbox[0] + bbox[2]) / 2 * screen_width), int((bbox[1] + bbox[3]) / 2 * screen_height)]
                img_to_show_data = base64.b64decode(img_to_show_base64)
                img_to_show = Image.open(BytesIO(img_to_show_data))

                draw = ImageDraw.Draw(img_to_show)
                x, y = vlm_response_json["box_centroid_coordinate"] 
                radius = 10
                draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill='red')
                draw.ellipse((x - radius*3, y - radius*3, x + radius*3, y + radius*3), fill=None, outline='red', width=2)

                buffered = BytesIO()
                img_to_show.save(buffered, format="PNG")
                img_to_show_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
            except:
                print(f"Error parsing: {vlm_response_json}")
                pass
        self.output_callback(f'<img src="data:image/png;base64,{img_to_show_base64}">', sender="bot")
        self.output_callback(
                    f'<details>'
                    f'  <summary>Parsed Screen elemetns by OmniParser</summary>'
                    f'  <pre>{screen_info}</pre>'
                    f'</details>',
                    sender="bot"
                )
        vlm_plan_str = ""
        for key, value in vlm_response_json.items():
            if key == "Reasoning":
                vlm_plan_str += f'{value}'
            else:
                vlm_plan_str += f'\n{key}: {value}'

        # construct the response so that anthropicExcutor can execute the tool
        response_content = [BetaTextBlock(text=vlm_plan_str, type='text')]
        if 'box_centroid_coordinate' in vlm_response_json:
            move_cursor_block = BetaToolUseBlock(id=f'toolu_{uuid.uuid4()}',
                                            input={'action': 'mouse_move', 'coordinate': vlm_response_json["box_centroid_coordinate"]},
                                            name='computer', type='tool_use')
            response_content.append(move_cursor_block)

        if vlm_response_json["Next Action"] == "None":
            print("Task paused/completed.")
        elif vlm_response_json["Next Action"] == "type":
            sim_content_block = BetaToolUseBlock(id=f'toolu_{uuid.uuid4()}',
                                        input={'action': vlm_response_json["Next Action"], 'text': vlm_response_json["value"]},
                                        name='computer', type='tool_use')
            response_content.append(sim_content_block)
        else:
            sim_content_block = BetaToolUseBlock(id=f'toolu_{uuid.uuid4()}',
                                            input={'action': vlm_response_json["Next Action"]},
                                            name='computer', type='tool_use')
            response_content.append(sim_content_block)
        response_message = BetaMessage(id=f'toolu_{uuid.uuid4()}', content=response_content, model='', role='assistant', type='message', stop_reason='tool_use', usage=BetaUsage(input_tokens=0, output_tokens=0))
        return response_message, vlm_response_json

    def _api_response_callback(self, response: APIResponse):
        self.api_response_callback(response)

    def _get_system_prompt(self, screen_info: str = ""):
        main_section = f"""
You are using a Windows device.
You are able to use a mouse and keyboard to interact with the computer based on the given task and screenshot.
You can only interact with the desktop GUI (no terminal or application menu access).

You may be given some history plan and actions, this is the response from the previous loop.
You should carefully consider your plan base on the task, screenshot, and history actions.

CRITICAL - FRAME COMPARISON LOGIC:
When you see multiple screenshots in the conversation history, you MUST compare the current screenshot with the previous one:
1. If the previous action was "double_click", "left_click", or any action that should cause a UI change
2. AND the current screenshot looks identical or very similar to the previous screenshot
3. AND no visible loading indicators, new windows, or interface changes are apparent
4. THEN you should use "wait" action to allow more time for the application/interface to respond
5. You should wait up to 3-4 times maximum before concluding that the action failed

This prevents executing the same action multiple times when the interface is simply slow to respond.

Here is the list of all detected bounding boxes by IDs on the screen and their description:{screen_info}

Your available "Next Action" only include:
- type: types a string of text.
- left_click: move mouse to box id and left clicks (for buttons, links, and UI elements).
- right_click: move mouse to box id and right clicks (for context menus).
- double_click: move mouse to box id and double clicks (REQUIRED for opening desktop application icons, files, and folders on Windows desktop).
- hover: move mouse to box id.
- scroll_up: scrolls the screen up to view previous content.
- scroll_down: scrolls the screen down, when the desired button is not visible, or you need to see more content. 
- wait: waits for 1 second for the device to load or respond.

IMPORTANT: On Windows desktop, to open application icons (like Chrome, Firefox, etc.), files, or folders, you MUST use "double_click" instead of "left_click".

Based on the visual information from the screenshot image and the detected bounding boxes, please determine the next action, the Box ID you should operate on (if action is one of 'type', 'hover', 'scroll_up', 'scroll_down', 'wait', there should be no Box ID field), and the value (if the action is 'type') in order to complete the task.

Output format:
```json
{{
    "Reasoning": str, # FIRST, compare the current screenshot with previous screenshots if available. If the previous action was double_click/left_click and the screen appears unchanged, consider using 'wait'. THEN describe what is in the current screen, taking into account the history, then describe your step-by-step thoughts on how to achieve the task, choose one action from available actions at a time.
    "Next Action": "action_type, action description" | "None" # one action at a time, describe it in short and precisely. Use 'wait' if the interface appears unchanged after a UI action.
    "Box ID": n,
    "value": "xxx" # only provide value field if the action is type, else don't include value key
}}
```

CRITICAL JSON FORMAT REQUIREMENTS:
1. Your response MUST be valid JSON format wrapped in ```json code blocks
2. Do NOT include trailing commas after the last property
3. Do NOT include comments (//) in the JSON
4. Use double quotes for all strings
5. Ensure all braces and brackets are properly matched
6. Do NOT include any text before or after the JSON code block
7. The JSON must be parseable by standard JSON parsers

EXAMPLES:
- CORRECT: {{"Box ID": 20}}
- INCORRECT: {{"Box ID": 20,}}
- CORRECT: {{"Next Action": "left_click"}}
- INCORRECT: {{"Next Action": 'left_click'}}

One Example:
```json
{{  
    "Reasoning": "The current screen shows google result of amazon, in previous action I have searched amazon on google. Then I need to click on the first search results to go to amazon.com.",
    "Next Action": "left_click",
    "Box ID": 1
}}
```

Another Example:
```json
{{
    "Reasoning": "The current screen shows the front page of amazon. There is no previous action. Therefore I need to type Apple watch in the search bar.",
    "Next Action": "type",
    "Box ID": 5,
    "value": "Apple watch"
}}
```

Another Example:
```json
{{
    "Reasoning": "The current screen does not show submit button, I need to scroll down to see if the button is available.",
    "Next Action": "scroll_down"
}}
```

FRAME COMPARISON Example:
```json
{{
    "Reasoning": "Looking at the current screenshot and comparing it with the previous one, I can see they are nearly identical. In the previous action, I double-clicked on the MSTorque application icon, but the desktop still looks the same with no new windows or loading indicators visible. The application may be taking time to launch. I should wait for the application to fully load before proceeding.",
    "Next Action": "wait"
}}
```

IMPORTANT NOTES:
1. You should only give a single action at a time.
2. CRITICAL: When you see application icons on the Windows desktop (like Chrome, Firefox, File Explorer, etc.), you MUST use "double_click" to open them. Single clicking desktop icons will NOT open applications.
3. Use "left_click" only for buttons, links, menu items, and other UI elements within applications.
4. If you repeatedly try "left_click" on a desktop icon and it doesn't open, switch to "double_click" immediately.
5. FRAME COMPARISON: Always compare the current screenshot with previous screenshots. If the interface appears unchanged after an action that should cause changes (like double_click, left_click), use "wait" to allow the interface to respond instead of repeating the action.
6. TIMING AWARENESS: Windows applications, especially desktop software, can take several seconds to launch. Don't assume an action failed just because the interface hasn't changed immediately.

"""
        thinking_model = "r1" in self.model
        if not thinking_model:
            main_section += """
5. You should give an analysis to the current screen, and reflect on what has been done by looking at the history, then describe your step-by-step thoughts on how to achieve the task.

"""
        else:
            main_section += """
5. In <think> XML tags give an analysis to the current screen, and reflect on what has been done by looking at the history, then describe your step-by-step thoughts on how to achieve the task. In <output> XML tags put the next action prediction JSON.

"""
        main_section += """
6. Attach the next action prediction in the "Next Action".
7. You should not include other actions, such as keyboard shortcuts.
8. When the task is completed, don't complete additional actions. You should say "Next Action": "None" in the json field.
9. The tasks involve buying multiple products or navigating through multiple pages. You should break it into subgoals and complete each subgoal one by one in the order of the instructions.
10. avoid choosing the same action/elements multiple times in a row, if it happens, reflect to yourself, what may have gone wrong, and predict a different action.
11. If you are prompted with login information page or captcha page, or you think it need user's permission to do the next action, you should say "Next Action": "None" in the json field.
12. CRITICAL FRAME LOGIC: Before deciding on any action, always examine if there are previous screenshots available. If the current and previous screenshots look very similar/identical AND the previous action was supposed to cause a UI change (like double_click, left_click), then you should use "wait" action to give the interface more time to respond. This prevents duplicate actions and allows for proper application loading times.
""" 

        return main_section

def _remove_som_images(messages):
    for msg in messages:
        msg_content = msg["content"]
        if isinstance(msg_content, list):
            msg["content"] = [
                cnt for cnt in msg_content 
                if not (isinstance(cnt, str) and 'som' in cnt and is_image_path(cnt))
            ]


def _maybe_filter_to_n_most_recent_images(
    messages: list[BetaMessageParam],
    images_to_keep: int,
    min_removal_threshold: int = 10,
):
    """
    With the assumption that images are screenshots that are of diminishing value as
    the conversation progresses, remove all but the final `images_to_keep` tool_result
    images in place
    """
    if images_to_keep is None:
        return messages

    total_images = 0
    for msg in messages:
        for cnt in msg.get("content", []):
            if isinstance(cnt, str) and is_image_path(cnt):
                total_images += 1
            elif isinstance(cnt, dict) and cnt.get("type") == "tool_result":
                for content in cnt.get("content", []):
                    if isinstance(content, dict) and content.get("type") == "image":
                        total_images += 1

    images_to_remove = total_images - images_to_keep
    
    for msg in messages:
        msg_content = msg["content"]
        if isinstance(msg_content, list):
            new_content = []
            for cnt in msg_content:
                # Remove images from SOM or screenshot as needed
                if isinstance(cnt, str) and is_image_path(cnt):
                    if images_to_remove > 0:
                        images_to_remove -= 1
                        continue
                # VLM shouldn't use anthropic screenshot tool so shouldn't have these but in case it does, remove as needed
                elif isinstance(cnt, dict) and cnt.get("type") == "tool_result":
                    new_tool_result_content = []
                    for tool_result_entry in cnt.get("content", []):
                        if isinstance(tool_result_entry, dict) and tool_result_entry.get("type") == "image":
                            if images_to_remove > 0:
                                images_to_remove -= 1
                                continue
                        new_tool_result_content.append(tool_result_entry)
                    cnt["content"] = new_tool_result_content
                # Append fixed content to current message's content list
                new_content.append(cnt)
            msg["content"] = new_content