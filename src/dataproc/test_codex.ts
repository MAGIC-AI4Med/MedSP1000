import { Codex } from "@openai/codex-sdk";
import { readFile } from "node:fs/promises";
import { parse as parseYaml } from "yaml";

const WORKING_DIRECTORY =
  "/mnt/petrelfs/liangcheng/data/simulate_eval/scrapy_meded/test_multi/mededportal_10046_copy";
const SANDBOX_MODE = (process.env.CODEX_SANDBOX_MODE ??
  "danger-full-access") as "read-only" | "workspace-write" | "danger-full-access";
const APPROVAL_POLICY = (process.env.CODEX_APPROVAL_POLICY ?? "never") as
  | "never"
  | "on-request"
  | "on-failure"
  | "untrusted";

type PromptConfig = {
  phase1?: string;
  phase2?: string;
  phase3?: string;
};

async function loadPrompts(filePath: string): Promise<PromptConfig> {
  const text = await readFile(filePath, "utf8");
  const parsed = parseYaml(text);
  if (!parsed || typeof parsed !== "object") {
    throw new Error("prompt.yaml must be a YAML object");
  }
  return parsed as PromptConfig;
}

function summarizeText(text: string, maxLen = 220): string {
  if (text.length <= maxLen) {
    return text;
  }
  return `${text.slice(0, maxLen)}...`;
}

async function runPhaseWithProgress(
  thread: ReturnType<Codex["startThread"]>,
  phase: string,
  prompt: string
) {
  console.log(`\n===== ${phase} START =====`);
  const { events } = await thread.runStreamed(prompt);

  const commandOutputOffsets = new Map<string, number>();
  let finalResponse = "";

  for await (const event of events) {
    if (event.type === "thread.started") {
      console.log(`[${phase}] thread.started id=${event.thread_id}`);
      continue;
    }

    if (event.type === "turn.started") {
      console.log(`[${phase}] turn.started`);
      continue;
    }

    if (event.type === "item.started") {
      const item = event.item;
      if (item.type === "command_execution") {
        console.log(`[${phase}] command.start: ${item.command}`);
        commandOutputOffsets.set(item.id, 0);
      } else if (item.type === "mcp_tool_call") {
        console.log(`[${phase}] mcp.start: ${item.server}/${item.tool}`);
      } else {
        console.log(`[${phase}] item.start: ${item.type}`);
      }
      continue;
    }

    if (event.type === "item.updated") {
      const item = event.item;
      if (item.type === "command_execution") {
        const output = item.aggregated_output ?? "";
        const prev = commandOutputOffsets.get(item.id) ?? 0;
        if (output.length > prev) {
          const delta = output.slice(prev).trim();
          if (delta) {
            console.log(`[${phase}] command.output: ${summarizeText(delta)}`);
          }
          commandOutputOffsets.set(item.id, output.length);
        }
      }
      continue;
    }

    if (event.type === "item.completed") {
      const item = event.item;
      if (item.type === "command_execution") {
        console.log(
          `[${phase}] command.end: status=${item.status} exit=${item.exit_code ?? "n/a"}`
        );
      } else if (item.type === "file_change") {
        const changed = item.changes.map((c) => `${c.kind}:${c.path}`).join(", ");
        console.log(`[${phase}] file_change: ${summarizeText(changed, 320)}`);
      } else if (item.type === "agent_message") {
        finalResponse = item.text ?? "";
        console.log(`[${phase}] agent_message: ${summarizeText(finalResponse)}`);
      } else if (item.type === "reasoning") {
        console.log(`[${phase}] reasoning: ${summarizeText(item.text ?? "")}`);
      } else {
        console.log(`[${phase}] item.end: ${item.type}`);
      }
      continue;
    }

    if (event.type === "turn.completed") {
      const u = event.usage;
      console.log(
        `[${phase}] turn.completed usage in=${u.input_tokens} out=${u.output_tokens} cached=${u.cached_input_tokens}`
      );
      continue;
    }

    if (event.type === "turn.failed") {
      throw new Error(`[${phase}] turn.failed: ${event.error.message}`);
    }

    if (event.type === "error") {
      throw new Error(`[${phase}] stream.error: ${event.message}`);
    }
  }

  console.log(`\n[${phase}] final response:\n${finalResponse}`);
  console.log(`===== ${phase} END =====`);
}

async function main() {
  const prompts = await loadPrompts("prompt.yaml");
  const phases = ["phase1", "phase2", "phase3"] as const;

  const codex = new Codex();
  const thread = codex.startThread({
    workingDirectory: WORKING_DIRECTORY,
    skipGitRepoCheck: true,
    sandboxMode: SANDBOX_MODE,
    approvalPolicy: APPROVAL_POLICY,
  });

  for (const phase of phases) {
    const prompt = prompts[phase];
    if (!prompt) {
      throw new Error(`missing prompt: ${phase}`);
    }
    await runPhaseWithProgress(thread, phase, prompt);
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
