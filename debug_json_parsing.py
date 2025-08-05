#!/usr/bin/env python3
"""
JSON解析调试工具
用于分析和调试VLM响应的JSON解析问题
"""

import json
import re
import sys
from typing import Optional


def extract_data_debug(input_string: str, data_type: str = "json") -> tuple[str, dict]:
    """
    提取代码块中的内容，并返回调试信息
    
    Returns:
        tuple: (extracted_content, debug_info)
    """
    debug_info = {
        "original_length": len(input_string),
        "has_markdown_blocks": False,
        "patterns_tried": [],
        "extraction_method": "none",
        "issues_found": []
    }
    
    if not input_string or not isinstance(input_string, str):
        debug_info["issues_found"].append("Empty or non-string input")
        return "", debug_info
    
    # 检查是否包含markdown代码块
    if "```" in input_string:
        debug_info["has_markdown_blocks"] = True
    
    # 尝试多种模式来提取JSON内容
    patterns = [
        (f"```{data_type}\\s*(.*?)\\s*```", "标准代码块格式"),
        (f"```\\s*({{.*?}})\\s*```", "无语言标识代码块"),
        (f"```{data_type}\\s*(.*?)$", "仅开始标记"),
        (r"(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})", "直接JSON内容"),
    ]
    
    for pattern, description in patterns:
        debug_info["patterns_tried"].append(description)
        matches = re.findall(pattern, input_string, re.DOTALL | re.IGNORECASE)
        if matches:
            content = matches[0]
            if isinstance(content, tuple):
                content = content[0]
            debug_info["extraction_method"] = description
            return content.strip(), debug_info
    
    # 如果没有找到代码块，尝试查找JSON对象
    json_start = input_string.find('{')
    if json_start != -1:
        debug_info["extraction_method"] = "查找完整JSON对象"
        brace_count = 0
        for i, char in enumerate(input_string[json_start:], json_start):
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    return input_string[json_start:i+1].strip(), debug_info
    
    debug_info["extraction_method"] = "返回原字符串"
    return input_string.strip(), debug_info


def analyze_json_parsing_issues(vlm_response: str) -> dict:
    """
    分析VLM响应的JSON解析问题
    
    Args:
        vlm_response: VLM的原始响应
        
    Returns:
        dict: 包含分析结果的字典
    """
    analysis = {
        "response_length": len(vlm_response),
        "response_preview": vlm_response[:200] + "..." if len(vlm_response) > 200 else vlm_response,
        "has_json_blocks": False,
        "has_json_content": False,
        "extraction_success": False,
        "parsing_success": False,
        "extracted_content": "",
        "json_object": None,
        "issues": [],
        "suggestions": []
    }
    
    # 提取JSON内容
    extracted_json, debug_info = extract_data_debug(vlm_response, "json")
    analysis["extraction_debug"] = debug_info
    analysis["extracted_content"] = extracted_json
    
    if extracted_json:
        analysis["extraction_success"] = True
        
        # 清理JSON内容
        cleaned_json = extracted_json.strip()
        
        # 移除markdown标记
        if cleaned_json.startswith('```json'):
            cleaned_json = cleaned_json[7:]
        if cleaned_json.startswith('```'):
            cleaned_json = cleaned_json[3:]
        if cleaned_json.endswith('```'):
            cleaned_json = cleaned_json[:-3]
        cleaned_json = cleaned_json.strip()
        
        # 移除尾随逗号
        cleaned_json = re.sub(r',(\s*[}\]])', r'\1', cleaned_json)
        
        # 检查常见问题
        if not cleaned_json:
            analysis["issues"].append("提取后的JSON内容为空")
        elif not cleaned_json.startswith('{'):
            analysis["issues"].append("JSON内容不以大括号开始")
        elif not cleaned_json.endswith('}'):
            analysis["issues"].append("JSON内容不以大括号结束")
        
        # 尝试解析JSON
        try:
            json_obj = json.loads(cleaned_json)
            analysis["parsing_success"] = True
            analysis["json_object"] = json_obj
        except json.JSONDecodeError as e:
            analysis["issues"].append(f"JSON解析错误: {str(e)}")
            analysis["suggestions"].append("检查JSON语法，特别是逗号、引号和括号匹配")
            
            # 尝试修复常见问题
            try:
                fixed_json = re.sub(r',(\s*)(?=})', r'\1', cleaned_json)
                fixed_json = re.sub(r'^[^{]*({.*})[^}]*$', r'\1', fixed_json, flags=re.DOTALL)
                json_obj = json.loads(fixed_json)
                analysis["parsing_success"] = True
                analysis["json_object"] = json_obj
                analysis["suggestions"].append("通过自动修复成功解析")
            except json.JSONDecodeError as e2:
                analysis["suggestions"].append(f"自动修复也失败: {str(e2)}")
    else:
        analysis["issues"].append("无法从响应中提取JSON内容")
        analysis["suggestions"].append("检查响应是否包含有效的JSON代码块")
    
    return analysis


def test_json_parsing():
    """测试JSON解析功能"""
    test_cases = [
        # 正常情况
        '''```json
{
    "Reasoning": "This is a test",
    "Next Action": "left_click",
    "Box ID": 5
}
```''',
        
        # 有尾随逗号
        '''```json
{
    "Reasoning": "This is a test",
    "Next Action": "left_click",
    "Box ID": 5,
}
```''',
        
        # 没有代码块标记
        '''
{
    "Reasoning": "This is a test",
    "Next Action": "left_click",
    "Box ID": 5
}
''',
        
        # 包含额外文本
        '''Here is my analysis:
```json
{
    "Reasoning": "This is a test",
    "Next Action": "left_click",
    "Box ID": 5
}
```
Hope this helps!''',
        
        # 空响应
        '',
        
        # 格式错误
        '''```json
{
    "Reasoning": "This is a test"
    "Next Action": "left_click",
    "Box ID": 5
}
```''',
    ]
    
    print("🧪 JSON解析测试开始")
    print("=" * 60)
    
    for i, test_case in enumerate(test_cases, 1):
        print(f"\n测试用例 {i}:")
        print("-" * 30)
        print(f"输入: {repr(test_case[:100])}")
        
        analysis = analyze_json_parsing_issues(test_case)
        
        print(f"✅ 提取成功: {analysis['extraction_success']}")
        print(f"✅ 解析成功: {analysis['parsing_success']}")
        
        if analysis['issues']:
            print(f"❌ 问题: {', '.join(analysis['issues'])}")
        
        if analysis['suggestions']:
            print(f"💡 建议: {', '.join(analysis['suggestions'])}")
        
        if analysis['json_object']:
            print(f"📝 解析结果: {analysis['json_object']}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # 分析命令行提供的文本
        text_to_analyze = " ".join(sys.argv[1:])
        analysis = analyze_json_parsing_issues(text_to_analyze)
        
        print("🔍 JSON解析分析结果")
        print("=" * 50)
        print(f"原始长度: {analysis['response_length']}")
        print(f"提取成功: {analysis['extraction_success']}")
        print(f"解析成功: {analysis['parsing_success']}")
        
        if analysis['issues']:
            print(f"\n❌ 发现的问题:")
            for issue in analysis['issues']:
                print(f"  - {issue}")
        
        if analysis['suggestions']:
            print(f"\n💡 建议:")
            for suggestion in analysis['suggestions']:
                print(f"  - {suggestion}")
        
        if analysis['json_object']:
            print(f"\n📝 解析结果:")
            print(json.dumps(analysis['json_object'], indent=2, ensure_ascii=False))
    else:
        # 运行测试用例
        test_json_parsing()
