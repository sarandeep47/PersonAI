# eval/run_eval.py
import json
import os
import sys
import time

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agent.core import call_agent

def run_eval():
    eval_file = os.path.join(os.path.dirname(__file__), "test_cases.json")
    with open(eval_file, "r") as f:
        test_cases = json.load(f)

    print("=" * 60)
    print(f"  Starting Evaluation Harness ({len(test_cases)} test cases)")
    print("=" * 60)

    correct_count = 0
    results = []

    start_time = time.time()

    for idx, case in enumerate(test_cases, 1):
        user_input = case["input"]
        expected = case["expected_tool"]
        
        try:
            res = call_agent(user_input)
            got = res.tool
            is_correct = (got == expected)
            if is_correct:
                correct_count += 1
                status = "✅ PASS"
            else:
                status = "❌ FAIL"
            
            print(f"[{idx:02d}/{len(test_cases)}] {status} | Expected: {expected:<12} | Got: {got:<12} | Input: {user_input}")
            results.append({
                "input": user_input,
                "expected": expected,
                "got": got,
                "correct": is_correct,
                "reasoning": res.reasoning
            })
        except Exception as e:
            print(f"[{idx:02d}/{len(test_cases)}] 💥 ERROR | Expected: {expected:<12} | Error: {e}")
            results.append({
                "input": user_input,
                "expected": expected,
                "error": str(e),
                "correct": False
            })

    elapsed = time.time() - start_time
    accuracy = (correct_count / len(test_cases)) * 100

    print("\n" + "=" * 60)
    print(f"  Evaluation Summary")
    print(f"  Accuracy: {accuracy:.1f}% ({correct_count}/{len(test_cases)})")
    print(f"  Total Time: {elapsed:.2f}s (Avg: {elapsed/len(test_cases):.2f}s/case)")
    print("=" * 60)

    # Save summary report
    report_path = os.path.join(os.path.dirname(__file__), "last_eval_report.json")
    with open(report_path, "w") as f:
        json.dump({"accuracy": accuracy, "elapsed": elapsed, "results": results}, f, indent=2)
    print(f"Report saved to {report_path}")

if __name__ == "__main__":
    run_eval()
