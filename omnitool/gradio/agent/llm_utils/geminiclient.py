import os
import base64
import contextlib
import threading
from google import genai
from typing import Optional, Dict, Any
import logging
from .utils import is_image_path, encode_image
from .gemini_config import GEMINI_PROXY_URL, GEMINI_PRICING

class ProxyManager:
    """线程安全的代理管理器"""
    
    def __init__(self, proxy_url: str):
        self.proxy_url = proxy_url
        self._lock = threading.Lock()
    
    @contextlib.contextmanager
    def set_proxy(self, verbose: bool = False):
        """线程安全的代理设置上下文管理器"""
        with self._lock:
            # 保存原始的代理设置
            original_proxies = {
                'HTTP_PROXY': os.environ.get('HTTP_PROXY'),
                'HTTPS_PROXY': os.environ.get('HTTPS_PROXY'),
                'http_proxy': os.environ.get('http_proxy'),
                'https_proxy': os.environ.get('https_proxy')
            }
            
            # 设置新的代理（同时设置大小写版本以确保兼容性）
            proxy_env_vars = {
                'HTTP_PROXY': self.proxy_url,
                'HTTPS_PROXY': self.proxy_url,
                'http_proxy': self.proxy_url,
                'https_proxy': self.proxy_url
            }
            
            os.environ.update(proxy_env_vars)
            
            if verbose:
                print(f"🔄 代理已临时设置为: {self.proxy_url}")
            
            try:
                yield
            finally:
                # 恢复原始的代理设置
                for key, original_value in original_proxies.items():
                    if original_value is not None:
                        os.environ[key] = original_value
                    elif key in os.environ:
                        del os.environ[key]
                
                if verbose:
                    print("✅ 代理已完全恢复")


def run_gemini_interleaved(messages: list, system: str, model_name: str, api_key: str, max_tokens=256, temperature=0, proxy_url: Optional[str] = None):
    """
    运行Gemini模型的交互式对话
    
    Args:
        messages: 消息列表
        system: 系统提示
        model_name: 模型名称
        api_key: API密钥
        max_tokens: 最大token数
        temperature: 温度参数
        proxy_url: 代理URL（可选）
    """
    
    # 从配置文件或环境变量获取代理URL（如果没有通过参数传递）
    if not proxy_url:
        proxy_url = os.getenv('GEMINI_PROXY_URL', GEMINI_PROXY_URL)
    
    # 如果代理URL为空字符串或None，则不使用代理
    if not proxy_url or proxy_url.strip() == "":
        proxy_manager = None
    else:
        proxy_manager = ProxyManager(proxy_url)
    
    def _prepare_contents(messages: list, system: str):
        """准备发送给Gemini的内容"""
        parts = []
        
        # 添加系统提示
        if system:
            parts.append({"text": system})
        
        # 处理消息
        for item in messages:
            if isinstance(item, dict) and "content" in item:
                for cnt in item["content"]:
                    if isinstance(cnt, str):
                        if is_image_path(cnt):
                            # 处理图片
                            try:
                                base64_image = encode_image(cnt)
                                
                                # 根据文件扩展名确定MIME类型
                                ext = os.path.splitext(cnt)[1].lower()
                                mime_type_map = {
                                    '.png': 'image/png',
                                    '.jpg': 'image/jpeg',
                                    '.jpeg': 'image/jpeg',
                                    '.gif': 'image/gif',
                                    '.webp': 'image/webp'
                                }
                                mime_type = mime_type_map.get(ext, 'image/png')
                                
                                parts.append({
                                    "inline_data": {
                                        "mime_type": mime_type,
                                        "data": base64_image
                                    }
                                })
                                print(f"处理图片: {cnt}")
                            except Exception as e:
                                print(f"处理图片失败 {cnt}: {e}")
                                # 如果图片处理失败，添加错误文本
                                parts.append({"text": f"[图片处理失败: {cnt}]"})
                        else:
                            # 处理文本
                            parts.append({"text": cnt})
                    else:
                        # 其他类型转为文本
                        parts.append({"text": str(cnt)})
            elif isinstance(item, str):
                parts.append({"text": item})
        
        return {"parts": parts}
    
    def _make_request():
        """发起API请求"""
        try:
            client = genai.Client(api_key=api_key)
            contents = _prepare_contents(messages, system)
            
            print(f"使用Gemini模型: {model_name}")
            
            response = client.models.generate_content(
                model=model_name,
                contents=contents
            )
            
            # 提取响应文本
            response_text = response.text if hasattr(response, 'text') else str(response)
            
            # Gemini API通常不返回token使用量，我们估算一个值
            estimated_tokens = len(response_text.split()) + sum(len(str(part).split()) for part in contents["parts"])
            
            print(f"Gemini响应成功，估算token使用量: {estimated_tokens}")
            return response_text, estimated_tokens
            
        except Exception as e:
            error_msg = f"Gemini API请求失败: {str(e)}"
            print(error_msg)
            return error_msg, 0
    
    try:
        if proxy_manager:
            with proxy_manager.set_proxy(verbose=False):
                return _make_request()
        else:
            return _make_request()
    except Exception as e:
        error_msg = f"运行Gemini客户端时出错: {str(e)}"
        print(error_msg)
        return error_msg, 0
