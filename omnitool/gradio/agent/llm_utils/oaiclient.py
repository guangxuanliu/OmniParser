import os
import logging
import base64
import requests
from .utils import is_image_path, encode_image

def run_oai_interleaved(messages: list, system: str, model_name: str, api_key: str, max_tokens=256, temperature=0, provider_base_url: str = "https://api.openai.com/v1"):    
    # For local Ollama deployment, we don't need API key
    is_local_ollama = "localhost" in provider_base_url or "127.0.0.1" in provider_base_url
    
    if is_local_ollama:
        headers = {"Content-Type": "application/json"}
        print(f"Using local Ollama deployment with model: {model_name}")
    else:
        headers = {"Content-Type": "application/json",
                   "Authorization": f"Bearer {api_key}"}
        print(f"Using online API with model: {model_name}")
        
    final_messages = [{"role": "system", "content": system}]

    if type(messages) == list:
        for item in messages:
            contents = []
            if isinstance(item, dict):
                for cnt in item["content"]:
                    if isinstance(cnt, str):
                        if is_image_path(cnt) and 'o3-mini' not in model_name:
                            # 统一处理图片，无论是本地还是在线模型
                            base64_image = encode_image(cnt)
                            content = {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                            print(f"处理图片: {cnt}")
                        else:
                            content = {"type": "text", "text": cnt}
                    else:
                        # in this case it is a text block from anthropic
                        content = {"type": "text", "text": str(cnt)}
                        
                    contents.append(content)
                    
                message = {"role": 'user', "content": contents}
            else:  # str
                contents.append({"type": "text", "text": item})
                message = {"role": "user", "content": contents}
            
            final_messages.append(message)

    
    elif isinstance(messages, str):
        final_messages = [{"role": "user", "content": messages}]

    payload = {
        "model": model_name,
        "messages": final_messages,
    }
    if 'o1' in model_name or 'o3-mini' in model_name:
        payload['reasoning_effort'] = 'low'
        payload['max_completion_tokens'] = max_tokens
    else:
        payload['max_tokens'] = max_tokens
    
    # 测试阶段2: 添加参数优化，看是否能改善模型行为
    if is_local_ollama:
        payload['temperature'] = 0.1  # 较低的温度提高稳定性和一致性
        payload['top_p'] = 0.9
        payload['stream'] = False
        # 适度限制 token 数量，但不要过于严格
        if 'max_tokens' in payload:
            payload['max_tokens'] = min(payload['max_tokens'], 1024)
        print(f"本地模型参数: temperature=0.1, max_tokens={payload.get('max_tokens', 'N/A')}")

    print(f"测试阶段2: 跳过图片处理 + 优化参数")
    response = requests.post(
        f"{provider_base_url}/chat/completions", headers=headers, json=payload
    )


    try:
        response_json = response.json()
        print(f"API 响应状态码: {response.status_code}")
        
        if response.status_code == 200:
            text = response_json['choices'][0]['message']['content']
            token_usage = int(response_json['usage']['total_tokens'])
            print(f"成功获取响应，token 使用量: {token_usage}")
            return text, token_usage
        else:
            error_msg = f"API 请求失败，状态码: {response.status_code}, 响应: {response_json}"
            print(error_msg)
            return error_msg, 0
            
    except Exception as e:
        error_msg = f"Error in interleaved openAI: {e}. This may due to your invalid API key. Please check the response: {response.json()}"
        print(error_msg)
        return error_msg, 0  # Return error message and 0 token usage