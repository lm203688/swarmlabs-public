"""
Comprehensive E2E Verification - All Sprints
Tests all modules: Sprint 1, 2, and 3
"""

import asyncio
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def run_sprint_tests(sprint_name: str, test_module: str):
    """运行单个Sprint的测试"""
    print(f"\n{'='*60}")
    print(f"Running {sprint_name} Tests")
    print(f"{'='*60}")
    
    try:
        # 动态导入测试模块
        module = __import__(f"scripts.verify_{test_module}", fromlist=["main"])
        exit_code = await module.main()
        return exit_code == 0
    except Exception as e:
        print(f"Error running {sprint_name} tests: {e}")
        return False


async def main():
    """运行所有测试"""
    print("=" * 70)
    print("Triple Model System - Comprehensive E2E Verification")
    print("=" * 70)
    
    start_time = time.time()
    
    # 运行所有Sprint测试
    sprints = [
        ("Sprint 1", "sprint1"),
        ("Sprint 2", "sprint2"),
        ("Sprint 3", "sprint3")
    ]
    
    results = []
    
    for sprint_name, test_module in sprints:
        success = await run_sprint_tests(sprint_name, test_module)
        results.append((sprint_name, success))
    
    # 打印总结
    total_time = time.time() - start_time
    
    print("\n" + "=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)
    
    total_sprints = len(results)
    passed_sprints = sum(1 for _, success in results if success)
    
    for sprint_name, success in results:
        status = "[OK]" if success else "[FAIL]"
        print(f"{status} {sprint_name}: {'PASSED' if success else 'FAILED'}")
    
    print(f"\nTotal: {passed_sprints}/{total_sprints} sprints passed")
    print(f"Total time: {total_time:.2f}s")
    
    if passed_sprints == total_sprints:
        print("\n[SUCCESS] All tests passed!")
        return 0
    else:
        print("\n[ERROR] Some tests failed!")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
