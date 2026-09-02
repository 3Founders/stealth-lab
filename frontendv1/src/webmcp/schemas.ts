import type { ToolName } from "./tools";

export interface ToolDef {
  name: ToolName;
  title: string;
  description: string;
  inputSchema: {
    type: "object";
    properties: Record<string, unknown>;
    required?: string[];
  };
}

export const TOOL_DEFS: ToolDef[] = [
  {
    name: "search_stealth",
    title: "Search Stealth",
    description:
      "Search Stealth for reusable procedures and tasks matching a goal. Returns ranked results with type, verification, and URLs.",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string", minLength: 1, description: "What you want to accomplish" },
        repository_id: { type: "string", description: "Optional repository context" },
        project_id: { type: "string", description: "Optional project context" },
      },
      required: ["query"],
    },
  },
  {
    name: "find_best_way",
    title: "Find best way",
    description:
      "Get the backend's recommended way to accomplish a goal, with the reasoning and alternatives. Use instead of inferring from search results.",
    inputSchema: {
      type: "object",
      properties: {
        goal: { type: "string", minLength: 1, description: "The goal to accomplish" },
        repository_id: { type: "string", description: "Optional repository scope" },
      },
      required: ["goal"],
    },
  },
  {
    name: "inspect_procedure",
    title: "Inspect procedure",
    description:
      "Get a procedure's goal, steps, verification, evidence summary, claims, and provenance.",
    inputSchema: {
      type: "object",
      properties: {
        procedure_id: { type: "string", minLength: 1 },
      },
      required: ["procedure_id"],
    },
  },
  {
    name: "inspect_task",
    title: "Inspect task",
    description:
      "Get a task's description, success criteria, capability statistics, known failure modes, and implementations.",
    inputSchema: {
      type: "object",
      properties: {
        task_id: { type: "string", minLength: 1 },
      },
      required: ["task_id"],
    },
  },
  {
    name: "inspect_solution",
    title: "Inspect solution",
    description:
      "Get the backend's full SolutionView for a solution id (a product-facing way to accomplish a goal).",
    inputSchema: {
      type: "object",
      properties: {
        solution_id: { type: "string", minLength: 1 },
      },
      required: ["solution_id"],
    },
  },
  {
    name: "inspect_evidence",
    title: "Inspect evidence",
    description:
      "Answer 'why should I trust this?': returns claims, verification summary, and evidence for a procedure, task, or implementation.",
    inputSchema: {
      type: "object",
      properties: {
        object_type: {
          type: "string",
          enum: ["procedure", "task", "implementation"],
        },
        object_id: { type: "string", minLength: 1 },
      },
      required: ["object_type", "object_id"],
    },
  },
  {
    name: "inspect_repository_knowledge",
    title: "Inspect repository knowledge",
    description:
      "Get what Stealth currently believes about a repository: claims, relevant procedures, conflicts. Optionally focused on a topic.",
    inputSchema: {
      type: "object",
      properties: {
        repository_id: { type: "string", minLength: 1 },
        focus: { type: "string", description: "Optional topic filter, e.g. 'authentication'" },
      },
      required: ["repository_id"],
    },
  },
  {
    name: "compare_implementations",
    title: "Compare implementations",
    description:
      "Compare verified implementations of a task by success rate, evidence, provider, and status, to make an informed selection.",
    inputSchema: {
      type: "object",
      properties: {
        task_id: { type: "string", minLength: 1 },
        implementation_ids: {
          type: "array",
          items: { type: "string" },
          description: "Optional subset to compare; default: all active",
        },
      },
      required: ["task_id"],
    },
  },
  {
    name: "find_problem",
    title: "Find problem",
    description:
      "Find Stealth problems (goals people are benchmarking solutions against) matching a query. Returns problem ids, status, and URLs.",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string", minLength: 1, description: "Problem topic or goal" },
      },
      required: ["query"],
    },
  },
  {
    name: "inspect_problem",
    title: "Inspect problem",
    description:
      "Get a problem's description, objective, current best verified solution (if any), and candidate solution count.",
    inputSchema: {
      type: "object",
      properties: {
        problem_id: { type: "string", minLength: 1 },
      },
      required: ["problem_id"],
    },
  },
  {
    name: "list_problem_solutions",
    title: "List problem solutions",
    description:
      "List candidate solutions proposed for a problem, with type and target reference.",
    inputSchema: {
      type: "object",
      properties: {
        problem_id: { type: "string", minLength: 1 },
      },
      required: ["problem_id"],
    },
  },
  {
    name: "compare_solutions",
    title: "Compare solutions",
    description:
      "Compare candidate solutions for a problem using the backend leaderboard (Wilson-lower verified success, runs, latency, cost). Only backend-declared comparable results are ranked; returns current_best (possibly empty) and per-entry states.",
    inputSchema: {
      type: "object",
      properties: {
        problem_id: { type: "string", minLength: 1 },
        benchmark_id: { type: "string", description: "Optional benchmark restriction" },
      },
      required: ["problem_id"],
    },
  },
  {
    name: "inspect_evaluation",
    title: "Inspect evaluation",
    description:
      "Get exactly how a benchmark result was obtained: metrics, run count, methodology, environment, verification summary, status.",
    inputSchema: {
      type: "object",
      properties: {
        evaluation_id: { type: "string", minLength: 1 },
      },
      required: ["evaluation_id"],
    },
  },
];
