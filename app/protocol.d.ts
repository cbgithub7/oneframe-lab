// Generated from contracts/protocol.json by scripts/protocol-types.js: do not edit it by hand.
// After a change to the contract, `node scripts/protocol-types.js --write` writes it again; `npm run check`
// fails while it is not current.

/** Each message as the engine or the app sends it, every field: what the main process reads. */
export declare namespace Wire {
  /** A failure as the engine or the app reports it: a kind and reason oneframe/errors.py declares, and what to do. */
  export interface Failure {
    kind: string;
    reason?: string;
    message: string;
    next?: string;
    retry: boolean;
    /** an engine bug's or a node's trace */
    detail?: string;
    /** a graph's, each against its node and port */
    problems?: Array<Problem>;
    /** the files a run lacks (kind files), or a remove would have touched (kind store) */
    files?: unknown[];
    bytes_left?: number;
    present_checkpoint?: string;
    /** the hosts a download's last attempt asked */
    hosts?: Array<string>;
    /** the bytes a failed download's partial still holds */
    kept?: number;
  }

  /** One problem of a graph, against its node and port. */
  export interface Problem {
    node: string;
    port: string;
    message: string;
  }

  /** An edge of a graph, by its ports (step.port). */
  export interface Edge {
    from: string;
    to: string;
  }

  /** A value a run made, read by its key through the value protocol (app/main/values.js). */
  export interface Value {
    type: string;
    /** Never reaches the page. */
    path: string;
    facets: { [key: string]: string };
    meta: { [key: string]: unknown };
    trust: string | null;
    key: string;
  }

  /** Where a node runs, at which values, and why (oneframe/memory/fit.py). */
  export interface Fit {
    outcome: string;
    device: string | null;
    kind: string | null;
    precision: string | null;
    values: { [key: string]: unknown };
    changes: Array<Change>;
    upgrades: Array<number>;
    skipped: Array<{ [key: string]: unknown }>;
    reduced: boolean;
    estimate: string;
    needs: { [key: string]: unknown } | null;
    devices: { [key: string]: { [key: string]: unknown } };
    warning: { [key: string]: unknown } | null;
    message: string;
    notes: Array<string>;
  }

  /** A change the fit made: its index in the node's memory model, and why. */
  export interface Change {
    change: number;
    why: string;
  }

  /** The person's control over the fit (oneframe/memory/settings.py). */
  export interface Settings {
    margin_mb: { [key: string]: number | null };
    never_reduce_quality: boolean;
    fit: string;
    notes?: Array<string>;
  }

  /** What the engine is: its version, its Python, its data root, its warnings, how many nodes it found and its methods. */
  export interface Hello {
    engine: string;
    python: string;
    /** Never reaches the page. */
    data: string;
    warnings: Array<string>;
    nodes: number;
    methods: Array<string>;
  }

  /** A node's manifest as its author wrote it, where it came from, and the params the engine added (oneframe/manifest.py). */
  export interface Manifest {
    id: string;
    version: string;
    title: string;
    category: string;
    summary?: string;
    inputs?: { [key: string]: unknown };
    outputs?: { [key: string]: unknown };
    params?: { [key: string]: unknown };
    run?: { [key: string]: unknown };
    devices?: Array<string>;
    memory?: { [key: string]: unknown };
    files?: Array<{ [key: string]: unknown }>;
    licence?: { [key: string]: unknown };
    links?: { [key: string]: unknown };
    /** Never reaches the page. */
    folder: string;
    /** Fields of its own, beside those declared. */
    [key: string]: unknown;
  }

  /** A kind of data, from contracts/port-types.json. */
  export interface PortType {
    doc: string;
    carrier: string;
    facets: { [key: string]: Array<string> };
  }

  /** A graph kept as a file: its name, whether it ships or the person saved it, its title, and whether it shadows a shipped one. */
  export interface GraphSummary {
    name: string;
    source: string;
    title: string;
    shadows?: string;
  }

  /** A port a graph's port could connect to, and how (oneframe/graph.py's fits). */
  export interface Connection {
    /** the node's key in the graph; absent for a node to add */
    step?: string;
    node: string;
    port: string;
    side: string;
    how: string;
    converter?: string;
    reason?: string;
    message?: string;
  }

  /** A runtime, its state on this machine, and the build this machine runs. */
  export interface Runtime {
    id: string;
    title: string;
    summary: string;
    python: string;
    status: string;
    build: string | null;
    size_bytes: number | null;
    on_disk: Array<string>;
    installed: { [key: string]: { [key: string]: unknown } };
    plan: Plan;
  }

  /** The build a runtime would install on this machine, or why none can. */
  export interface Plan {
    runtime: string;
    build: string | null;
    why: string;
    blocked: string | null;
    considered: Array<{ [key: string]: unknown }>;
    notes: Array<string>;
  }

  /** One file of a model job, and how it ended. */
  export interface FileOutcome {
    name: string | null;
    sha256: string;
    size: number;
    outcome: string;
    failure?: Failure;
  }

  /** The answer to a queued request that a stop, or the engine's close, took off the queue before it began. */
  export interface Stopped {
    stopped: boolean;
  }

  /** The engine started; what it is. Sent by the engine. */
  export interface EngineReadyEvent extends Hello {
    event: "engine.ready";
  }

  /** The engine cannot start, or the app could not start it. Sent by the engine or the app. */
  export interface EngineFailedEvent extends Failure {
    event: "engine.failed";
  }

  /** Something the engine was sent that it could not read. Sent by the engine. */
  export interface EngineWarningEvent {
    event: "engine.warning";
    message: string;
  }

  /** The engine stopped after it had started. Sent by the app. */
  export interface EngineExitEvent {
    event: "engine.exit";
    code: number | null;
  }

  /** A run began: its steps in order, and the converters the plan inserted. Sent by the engine. */
  export interface RunStartEvent {
    event: "run.start";
    run: string;
    order: Array<string>;
    notes: Array<string>;
  }

  /** A run ended with every step done: each step's values, by port. Sent by the engine. */
  export interface RunDoneEvent {
    event: "run.done";
    run: string;
    outputs: { [key: string]: { [key: string]: Value } };
  }

  /** A run ended with a failure: the first one, its step or edge, and the steps that failed and were skipped; or an engine bug. Sent by the engine. */
  export interface RunFailedEvent extends Failure {
    event: "run.failed";
    run: string;
    step?: string;
    edge?: Edge;
    failed?: Array<string>;
    skipped?: Array<string>;
  }

  /** A run was stopped, at this step. Sent by the engine. */
  export interface RunStoppedEvent {
    event: "run.stopped";
    run: string;
    step: string;
  }

  /** A step's result came from the cache: at its own values, or, with its fit, a reduced one the fit allows. Sent by the engine. */
  export interface NodeCachedEvent {
    event: "node.cached";
    run: string;
    step: string;
    node: string;
    key: string;
    fit?: Fit;
    outputs: { [key: string]: Value };
  }

  /** A step was fitted to the memory this machine has free, before an attempt. Sent by the engine. */
  export interface NodeFitEvent extends Fit {
    event: "node.fit";
    run: string;
    step: string;
    node: string;
    attempt: number;
    settings: Settings;
  }

  /** An attempt at a step began. Sent by the engine. */
  export interface NodeStartEvent {
    event: "node.start";
    run: string;
    step: string;
    node: string;
    where: string;
    key: string;
    attempt: number;
  }

  /** A node said what it is doing (ctx.stage), passed on by the scheduler with its step. Sent by the engine. */
  export interface StageEvent {
    event: "stage";
    run: string;
    step: string;
    stage: string;
    message: string;
  }

  /** A node's progress in its stage (ctx.progress, or a tqdm bar in a runtime), passed on with its step. Sent by the engine. */
  export interface ProgressEvent {
    event: "progress";
    run: string;
    step: string;
    stage: string;
    done: number | null;
    total: number | null;
    what: string;
  }

  /** The cap a runtime node's process put on torch's allocator on the card, or why there is none. Sent by the engine. */
  export interface CeilingEvent {
    event: "ceiling";
    run: string;
    step: string;
    applied: boolean;
    budget_mb: number | null;
    why?: string;
    free_mb?: number;
    reserve_mb?: number;
    outside_torch_mb?: number;
    vram_cap_mb?: number;
    vram_total_mb?: number;
  }

  /** A way of a node's step ran out of memory, and the node moved on to its next way (ctx.fallbacks). Sent by the engine. */
  export interface NodeStepOomEvent {
    event: "node.step_oom";
    run: string;
    step: string;
    stage: string;
    way: string;
    next: string;
    peak_reserved_mb: number | null;
    message: string;
  }

  /** An attempt finished: its time, its peaks, how it was made, its fit and its values. Sent by the engine. */
  export interface NodeDoneEvent {
    event: "node.done";
    run: string;
    step: string;
    node: string;
    key: string;
    attempt: number;
    seconds: number;
    peak_vram_mb: number | null;
    peak_reserved_mb: number | null;
    peak_ram_mb: number | null;
    made_with: { [key: string]: unknown };
    fit?: Fit;
    outputs: { [key: string]: Value };
  }

  /** An attempt ran out of memory: before the one retry, or the failure. Sent by the engine. */
  export interface NodeOomEvent {
    event: "node.oom";
    run: string;
    step: string;
    attempt: number;
    peak_reserved_mb: number | null;
    peak_ram_mb: number | null;
    message: string;
  }

  /** A step failed: its failure, each fit and each failed attempt's peaks. Sent by the engine. */
  export interface NodeFailedEvent extends Failure {
    event: "node.failed";
    run: string;
    step: string;
    node: string;
    fits?: Array<Fit>;
    peaks?: Array<{ [key: string]: unknown }>;
  }

  /** A step never started: a step it needs failed or was skipped (waited_on), or a value did not fit its input (edge, with the failure). Sent by the engine. */
  export interface NodeSkippedEvent extends Partial<Failure> {
    event: "node.skipped";
    run: string;
    step: string;
    node: string;
    waited_on?: string;
    edge?: Edge;
  }

  /** An install began. Sent by the engine. */
  export interface RuntimeStartEvent {
    event: "runtime.start";
    runtime: string;
    build: string;
  }

  /** An install's step began. Sent by the engine. */
  export interface RuntimeStepEvent {
    event: "runtime.step";
    runtime: string;
    build: string;
    step: string;
    index: number;
    total: number;
    message: string;
  }

  /** A line uv wrote during an install. Sent by the engine. */
  export interface RuntimeLogEvent {
    event: "runtime.log";
    runtime: string;
    build: string;
    line: string;
  }

  /** A runtime's source download, in bytes. Sent by the engine. */
  export interface RuntimeProgressEvent {
    event: "runtime.progress";
    runtime: string;
    build: string;
    step: string;
    what: string;
    done: number;
    total: number | null;
  }

  /** An install finished. Sent by the engine. */
  export interface RuntimeDoneEvent {
    event: "runtime.done";
    runtime: string;
    build: string;
    python: string;
    size_bytes: number;
    seconds: number;
  }

  /** An install failed, or the engine did while installing. Sent by the engine. */
  export interface RuntimeFailedEvent extends Failure {
    event: "runtime.failed";
    runtime: string;
    build: string;
  }

  /** An install was stopped; installing again picks up where it was. Sent by the engine. */
  export interface RuntimeStoppedEvent {
    event: "runtime.stopped";
    runtime: string;
    build: string;
    message: string;
  }

  /** A model job began: a download or a verify, and its files. Sent by the engine. */
  export interface ModelStartEvent {
    event: "model.start";
    job: string;
    what: string;
    node: string | null;
    files: Array<FileOutcome>;
    bytes: number;
  }

  /** A file of a model job reached a phase: checking, downloading, retrying, verifying, placing, or failed (with its failure). Sent by the engine. */
  export interface ModelFileEvent extends Partial<Failure> {
    event: "model.file";
    job: string;
    what?: string;
    node?: string | null;
    file: string | null;
    sha256: string;
    phase: string;
    bytes?: number;
    from?: number;
    total?: number | null;
    attempt?: number;
    wait_s?: number;
    why?: string;
  }

  /** A file of a model job, in bytes, throttled. Sent by the engine. */
  export interface ModelProgressEvent {
    event: "model.progress";
    job: string;
    file: string | null;
    sha256: string;
    bytes: number;
    total: number | null;
  }

  /** A model job finished: every file's outcome, and for a verify the files it dropped. Sent by the engine. */
  export interface ModelDoneEvent {
    event: "model.done";
    job: string;
    what: string;
    node: string | null;
    files: Array<FileOutcome>;
    dropped?: Array<string>;
  }

  /** A model job ended with a failure: the first failed file's, or an engine bug. Sent by the engine. */
  export interface ModelFailedEvent extends Failure {
    event: "model.failed";
    job: string;
    what?: string;
    node?: string | null;
    files: Array<FileOutcome>;
  }

  /** A model job was stopped; a download resumes where it stopped. Sent by the engine. */
  export interface ModelStoppedEvent {
    event: "model.stopped";
    job: string;
    what: string;
    node: string | null;
    files: Array<FileOutcome>;
    message: string;
  }

  /** Every event, told apart by `event`. */
  export type Event =
    | EngineReadyEvent
    | EngineFailedEvent
    | EngineWarningEvent
    | EngineExitEvent
    | RunStartEvent
    | RunDoneEvent
    | RunFailedEvent
    | RunStoppedEvent
    | NodeCachedEvent
    | NodeFitEvent
    | NodeStartEvent
    | StageEvent
    | ProgressEvent
    | CeilingEvent
    | NodeStepOomEvent
    | NodeDoneEvent
    | NodeOomEvent
    | NodeFailedEvent
    | NodeSkippedEvent
    | RuntimeStartEvent
    | RuntimeStepEvent
    | RuntimeLogEvent
    | RuntimeProgressEvent
    | RuntimeDoneEvent
    | RuntimeFailedEvent
    | RuntimeStoppedEvent
    | ModelStartEvent
    | ModelFileEvent
    | ModelProgressEvent
    | ModelDoneEvent
    | ModelFailedEvent
    | ModelStoppedEvent;

  export type EventName = Event["event"];

  /** Every method, its params and its answer. */
  export interface Methods {
    /** What the engine is. */
    "engine.hello": {
      params: { [key: string]: never };
      answer: Hello;
    };
    /** Every node the engine found, and the problems of those it could not read, by file. Queued, so a stop may answer it {stopped: true}. */
    "nodes.list": {
      params: { [key: string]: never };
      answer: {
        nodes: Array<Manifest>;
        problems: { [key: string]: Array<string> };
      } | Stopped;
    };
    /** Look for nodes again; what nodes.list answers. Queued, so a stop may answer it {stopped: true}. */
    "nodes.reload": {
      params: { [key: string]: never };
      answer: {
        nodes: Array<Manifest>;
        problems: { [key: string]: Array<string> };
      } | Stopped;
    };
    /** The fit a node would get on this machine now, at these params and for inputs of this meta. Queued, so a stop may answer it {stopped: true}. */
    "nodes.fit": {
      params: {
        node: string;
        params?: { [key: string]: unknown };
        inputs?: { [key: string]: { [key: string]: unknown } };
      };
      answer: Fit & {
        node: string;
        settings: Settings;
      } | Stopped;
    };
    /** Clear what this machine learned about a node; how many entries went. Queued, so a stop may answer it {stopped: true}. */
    "nodes.forget": {
      params: {
        node: string;
      };
      answer: {
        node: string | null;
        dropped: number;
      } | Stopped;
    };
    /** The port types, by name. Queued, so a stop may answer it {stopped: true}. */
    "ports.list": {
      params: { [key: string]: never };
      answer: {
        types: { [key: string]: PortType };
      } | Stopped;
    };
    /** A graph's plan: its steps in order, the converters inserted, and the edges checked only once their value arrives. Queued, so a stop may answer it {stopped: true}. */
    "graph.validate": {
      params: {
        graph: { [key: string]: unknown };
      };
      answer: {
        order: Array<string>;
        notes: Array<string>;
        depends: Array<Edge>;
      } | Stopped;
    };
    /** What a port of a graph's node could connect to, in the graph and by adding a node. Queued, so a stop may answer it {stopped: true}. */
    "graph.fits": {
      params: {
        graph: { [key: string]: unknown };
        port: string;
      };
      answer: {
        port: string;
        graph: Array<Connection>;
        add: Array<Connection>;
      } | Stopped;
    };
    /** Run a graph; its id at once, and its progress as run and node events. Queued, so a stop may answer it {stopped: true}. */
    "graph.run": {
      params: {
        graph: { [key: string]: unknown };
      };
      answer: {
        run: string;
      } | Stopped;
    };
    /** Every graph that ships and every one the person saved, and each broken file. Queued, so a stop may answer it {stopped: true}. */
    "graphs.list": {
      params: { [key: string]: never };
      answer: {
        graphs: Array<GraphSummary>;
        problems: { [key: string]: Array<string> };
      } | Stopped;
    };
    /** A graph by name. Queued, so a stop may answer it {stopped: true}. */
    "graphs.get": {
      params: {
        name: string;
      };
      answer: GraphSummary & {
        graph: { [key: string]: unknown };
      } | Stopped;
    };
    /** Save the person's graph by name, under the data root. Queued, so a stop may answer it {stopped: true}. */
    "graphs.save": {
      params: {
        name: string;
        graph: { [key: string]: unknown };
      };
      answer: GraphSummary | Stopped;
    };
    /** Delete the person's own graph by name; the shipped one of that name, if any, is then the one listed. Queued, so a stop may answer it {stopped: true}. */
    "graphs.remove": {
      params: {
        name: string;
      };
      answer: {
        name: string;
        removed: boolean;
        now: GraphSummary | null;
      } | Stopped;
    };
    /** Copy a file into the data root's inputs, by its content; its id and its name. Only the main process asks it. Queued, so a stop may answer it {stopped: true}. */
    "inputs.import": {
      params: {
        /** Never reaches the page. */
        path: string;
      };
      answer: {
        id: string;
        name: string;
      } | Stopped;
    };
    /** The file of a value a run committed, for the value protocol. Only the main process asks it. */
    "values.locate": {
      params: {
        key: string;
        port: string;
      };
      answer: {
        /** Never reaches the page. */
        path: string;
        type: string;
        carrier: string | null;
      };
    };
    /** Stop the running graph (this one, when it names a run). */
    "run.stop": {
      params: {
        run?: string;
      };
      answer: {
        stopping: boolean;
        run?: string;
      };
    };
    /** Every runtime, its state and plan on this machine, the problems of those that cannot be read, and the machine. Queued, so a stop may answer it {stopped: true}. */
    "runtimes.list": {
      params: { [key: string]: never };
      answer: {
        runtimes: Array<Runtime>;
        problems: { [key: string]: Array<string> };
        profile: { [key: string]: unknown };
      } | Stopped;
    };
    /** The build each runtime, or this one, would install on this machine. Queued, so a stop may answer it {stopped: true}. */
    "runtimes.plan": {
      params: {
        runtime?: string;
        refresh?: boolean;
      };
      answer: {
        plans: Array<Plan>;
        profile: { [key: string]: unknown };
      } | Stopped;
    };
    /** Install a runtime; the build at once, and its progress as runtime events, or already when it is installed. Queued, so a stop may answer it {stopped: true}. */
    "runtimes.install": {
      params: {
        runtime: string;
        build?: string;
      };
      answer: {
        runtime: string;
        build?: string;
        already?: boolean;
      } | Stopped;
    };
    /** Stop a runtime's install. */
    "runtimes.stop": {
      params: {
        runtime: string;
      };
      answer: {
        stopping: boolean;
      };
    };
    /** Delete a runtime's folder under the data root. Queued, so a stop may answer it {stopped: true}. */
    "runtimes.remove": {
      params: {
        runtime: string;
      };
      answer: {
        runtime: string;
        removed: string | null;
      } | Stopped;
    };
    /** Fetch a node's files, those of these checkpoints or all; the job at once, and its progress as model events. Queued, so a stop may answer it {stopped: true}. */
    "models.download": {
      params: {
        node: string;
        /** a list of checkpoints, or "all" */
        checkpoints?: Array<string> | string;
      };
      answer: {
        job: string;
        files: Array<FileOutcome>;
      } | Stopped;
    };
    /** Hash again what the store holds of a node, or all of it; the job at once. Queued, so a stop may answer it {stopped: true}. */
    "models.verify": {
      params: {
        node?: string;
      };
      answer: {
        job: string;
        files: Array<FileOutcome>;
      } | Stopped;
    };
    /** Stop the model job (this one, when it names a job). */
    "models.stop": {
      params: {
        job?: string;
      };
      answer: {
        stopping: boolean;
        job?: string;
      };
    };
    /** What the store holds for each node, and what no node pins, without the network. Queued, so a stop may answer it {stopped: true}. */
    "models.list": {
      params: { [key: string]: never };
      answer: {
        store: string;
        free_bytes: number;
        nodes: Array<{ [key: string]: unknown }>;
        unpinned: { [key: string]: unknown };
        problems: { [key: string]: Array<string> };
      } | Stopped;
    };
    /** Delete a node's files, or a checkpoint's, or exactly the unpinned files models.list showed. Queued, so a stop may answer it {stopped: true}. */
    "models.remove": {
      params: {
        node?: string;
        checkpoint?: string;
        unpinned?: Array<string>;
      };
      answer: {
        node?: string;
        checkpoint?: string | null;
        kept?: Array<{ [key: string]: unknown }>;
        unpinned?: Array<string>;
        removed: Array<{ [key: string]: unknown }>;
        failed: Array<{ [key: string]: unknown }>;
        freed: number;
      } | Stopped;
    };
    /** The system's open dialog, then the engine's import of the photo chosen: its id and name, or null when none was chosen. Answered by the app. */
    "photo.pick": {
      params: { [key: string]: never };
      answer: {
        id: string;
        name: string;
      } | null;
    };
  }

  export type MethodName = keyof Methods;

  export type Params<M extends MethodName> = Methods[M]["params"];

  export type Answer<M extends MethodName> = Methods[M]["answer"];
}

/** Each message as the page receives it, without the fields marked "page": false (door.js). */
export declare namespace Page {
  /** A failure as the engine or the app reports it: a kind and reason oneframe/errors.py declares, and what to do. */
  export interface Failure {
    kind: string;
    reason?: string;
    message: string;
    next?: string;
    retry: boolean;
    /** an engine bug's or a node's trace */
    detail?: string;
    /** a graph's, each against its node and port */
    problems?: Array<Problem>;
    /** the files a run lacks (kind files), or a remove would have touched (kind store) */
    files?: unknown[];
    bytes_left?: number;
    present_checkpoint?: string;
    /** the hosts a download's last attempt asked */
    hosts?: Array<string>;
    /** the bytes a failed download's partial still holds */
    kept?: number;
  }

  /** One problem of a graph, against its node and port. */
  export interface Problem {
    node: string;
    port: string;
    message: string;
  }

  /** An edge of a graph, by its ports (step.port). */
  export interface Edge {
    from: string;
    to: string;
  }

  /** A value a run made, read by its key through the value protocol (app/main/values.js). */
  export interface Value {
    type: string;
    facets: { [key: string]: string };
    meta: { [key: string]: unknown };
    trust: string | null;
    key: string;
  }

  /** Where a node runs, at which values, and why (oneframe/memory/fit.py). */
  export interface Fit {
    outcome: string;
    device: string | null;
    kind: string | null;
    precision: string | null;
    values: { [key: string]: unknown };
    changes: Array<Change>;
    upgrades: Array<number>;
    skipped: Array<{ [key: string]: unknown }>;
    reduced: boolean;
    estimate: string;
    needs: { [key: string]: unknown } | null;
    devices: { [key: string]: { [key: string]: unknown } };
    warning: { [key: string]: unknown } | null;
    message: string;
    notes: Array<string>;
  }

  /** A change the fit made: its index in the node's memory model, and why. */
  export interface Change {
    change: number;
    why: string;
  }

  /** The person's control over the fit (oneframe/memory/settings.py). */
  export interface Settings {
    margin_mb: { [key: string]: number | null };
    never_reduce_quality: boolean;
    fit: string;
    notes?: Array<string>;
  }

  /** What the engine is: its version, its Python, its data root, its warnings, how many nodes it found and its methods. */
  export interface Hello {
    engine: string;
    python: string;
    warnings: Array<string>;
    nodes: number;
    methods: Array<string>;
  }

  /** A node's manifest as its author wrote it, where it came from, and the params the engine added (oneframe/manifest.py). */
  export interface Manifest {
    id: string;
    version: string;
    title: string;
    category: string;
    summary?: string;
    inputs?: { [key: string]: unknown };
    outputs?: { [key: string]: unknown };
    params?: { [key: string]: unknown };
    run?: { [key: string]: unknown };
    devices?: Array<string>;
    memory?: { [key: string]: unknown };
    files?: Array<{ [key: string]: unknown }>;
    licence?: { [key: string]: unknown };
    links?: { [key: string]: unknown };
    /** Fields of its own, beside those declared. */
    [key: string]: unknown;
  }

  /** A kind of data, from contracts/port-types.json. */
  export interface PortType {
    doc: string;
    carrier: string;
    facets: { [key: string]: Array<string> };
  }

  /** A graph kept as a file: its name, whether it ships or the person saved it, its title, and whether it shadows a shipped one. */
  export interface GraphSummary {
    name: string;
    source: string;
    title: string;
    shadows?: string;
  }

  /** A port a graph's port could connect to, and how (oneframe/graph.py's fits). */
  export interface Connection {
    /** the node's key in the graph; absent for a node to add */
    step?: string;
    node: string;
    port: string;
    side: string;
    how: string;
    converter?: string;
    reason?: string;
    message?: string;
  }

  /** A runtime, its state on this machine, and the build this machine runs. */
  export interface Runtime {
    id: string;
    title: string;
    summary: string;
    python: string;
    status: string;
    build: string | null;
    size_bytes: number | null;
    on_disk: Array<string>;
    installed: { [key: string]: { [key: string]: unknown } };
    plan: Plan;
  }

  /** The build a runtime would install on this machine, or why none can. */
  export interface Plan {
    runtime: string;
    build: string | null;
    why: string;
    blocked: string | null;
    considered: Array<{ [key: string]: unknown }>;
    notes: Array<string>;
  }

  /** One file of a model job, and how it ended. */
  export interface FileOutcome {
    name: string | null;
    sha256: string;
    size: number;
    outcome: string;
    failure?: Failure;
  }

  /** The answer to a queued request that a stop, or the engine's close, took off the queue before it began. */
  export interface Stopped {
    stopped: boolean;
  }

  /** The engine started; what it is. Sent by the engine. */
  export interface EngineReadyEvent extends Hello {
    event: "engine.ready";
  }

  /** The engine cannot start, or the app could not start it. Sent by the engine or the app. */
  export interface EngineFailedEvent extends Failure {
    event: "engine.failed";
  }

  /** Something the engine was sent that it could not read. Sent by the engine. */
  export interface EngineWarningEvent {
    event: "engine.warning";
    message: string;
  }

  /** The engine stopped after it had started. Sent by the app. */
  export interface EngineExitEvent {
    event: "engine.exit";
    code: number | null;
  }

  /** A run began: its steps in order, and the converters the plan inserted. Sent by the engine. */
  export interface RunStartEvent {
    event: "run.start";
    run: string;
    order: Array<string>;
    notes: Array<string>;
  }

  /** A run ended with every step done: each step's values, by port. Sent by the engine. */
  export interface RunDoneEvent {
    event: "run.done";
    run: string;
    outputs: { [key: string]: { [key: string]: Value } };
  }

  /** A run ended with a failure: the first one, its step or edge, and the steps that failed and were skipped; or an engine bug. Sent by the engine. */
  export interface RunFailedEvent extends Failure {
    event: "run.failed";
    run: string;
    step?: string;
    edge?: Edge;
    failed?: Array<string>;
    skipped?: Array<string>;
  }

  /** A run was stopped, at this step. Sent by the engine. */
  export interface RunStoppedEvent {
    event: "run.stopped";
    run: string;
    step: string;
  }

  /** A step's result came from the cache: at its own values, or, with its fit, a reduced one the fit allows. Sent by the engine. */
  export interface NodeCachedEvent {
    event: "node.cached";
    run: string;
    step: string;
    node: string;
    key: string;
    fit?: Fit;
    outputs: { [key: string]: Value };
  }

  /** A step was fitted to the memory this machine has free, before an attempt. Sent by the engine. */
  export interface NodeFitEvent extends Fit {
    event: "node.fit";
    run: string;
    step: string;
    node: string;
    attempt: number;
    settings: Settings;
  }

  /** An attempt at a step began. Sent by the engine. */
  export interface NodeStartEvent {
    event: "node.start";
    run: string;
    step: string;
    node: string;
    where: string;
    key: string;
    attempt: number;
  }

  /** A node said what it is doing (ctx.stage), passed on by the scheduler with its step. Sent by the engine. */
  export interface StageEvent {
    event: "stage";
    run: string;
    step: string;
    stage: string;
    message: string;
  }

  /** A node's progress in its stage (ctx.progress, or a tqdm bar in a runtime), passed on with its step. Sent by the engine. */
  export interface ProgressEvent {
    event: "progress";
    run: string;
    step: string;
    stage: string;
    done: number | null;
    total: number | null;
    what: string;
  }

  /** The cap a runtime node's process put on torch's allocator on the card, or why there is none. Sent by the engine. */
  export interface CeilingEvent {
    event: "ceiling";
    run: string;
    step: string;
    applied: boolean;
    budget_mb: number | null;
    why?: string;
    free_mb?: number;
    reserve_mb?: number;
    outside_torch_mb?: number;
    vram_cap_mb?: number;
    vram_total_mb?: number;
  }

  /** A way of a node's step ran out of memory, and the node moved on to its next way (ctx.fallbacks). Sent by the engine. */
  export interface NodeStepOomEvent {
    event: "node.step_oom";
    run: string;
    step: string;
    stage: string;
    way: string;
    next: string;
    peak_reserved_mb: number | null;
    message: string;
  }

  /** An attempt finished: its time, its peaks, how it was made, its fit and its values. Sent by the engine. */
  export interface NodeDoneEvent {
    event: "node.done";
    run: string;
    step: string;
    node: string;
    key: string;
    attempt: number;
    seconds: number;
    peak_vram_mb: number | null;
    peak_reserved_mb: number | null;
    peak_ram_mb: number | null;
    made_with: { [key: string]: unknown };
    fit?: Fit;
    outputs: { [key: string]: Value };
  }

  /** An attempt ran out of memory: before the one retry, or the failure. Sent by the engine. */
  export interface NodeOomEvent {
    event: "node.oom";
    run: string;
    step: string;
    attempt: number;
    peak_reserved_mb: number | null;
    peak_ram_mb: number | null;
    message: string;
  }

  /** A step failed: its failure, each fit and each failed attempt's peaks. Sent by the engine. */
  export interface NodeFailedEvent extends Failure {
    event: "node.failed";
    run: string;
    step: string;
    node: string;
    fits?: Array<Fit>;
    peaks?: Array<{ [key: string]: unknown }>;
  }

  /** A step never started: a step it needs failed or was skipped (waited_on), or a value did not fit its input (edge, with the failure). Sent by the engine. */
  export interface NodeSkippedEvent extends Partial<Failure> {
    event: "node.skipped";
    run: string;
    step: string;
    node: string;
    waited_on?: string;
    edge?: Edge;
  }

  /** An install began. Sent by the engine. */
  export interface RuntimeStartEvent {
    event: "runtime.start";
    runtime: string;
    build: string;
  }

  /** An install's step began. Sent by the engine. */
  export interface RuntimeStepEvent {
    event: "runtime.step";
    runtime: string;
    build: string;
    step: string;
    index: number;
    total: number;
    message: string;
  }

  /** A line uv wrote during an install. Sent by the engine. */
  export interface RuntimeLogEvent {
    event: "runtime.log";
    runtime: string;
    build: string;
    line: string;
  }

  /** A runtime's source download, in bytes. Sent by the engine. */
  export interface RuntimeProgressEvent {
    event: "runtime.progress";
    runtime: string;
    build: string;
    step: string;
    what: string;
    done: number;
    total: number | null;
  }

  /** An install finished. Sent by the engine. */
  export interface RuntimeDoneEvent {
    event: "runtime.done";
    runtime: string;
    build: string;
    python: string;
    size_bytes: number;
    seconds: number;
  }

  /** An install failed, or the engine did while installing. Sent by the engine. */
  export interface RuntimeFailedEvent extends Failure {
    event: "runtime.failed";
    runtime: string;
    build: string;
  }

  /** An install was stopped; installing again picks up where it was. Sent by the engine. */
  export interface RuntimeStoppedEvent {
    event: "runtime.stopped";
    runtime: string;
    build: string;
    message: string;
  }

  /** A model job began: a download or a verify, and its files. Sent by the engine. */
  export interface ModelStartEvent {
    event: "model.start";
    job: string;
    what: string;
    node: string | null;
    files: Array<FileOutcome>;
    bytes: number;
  }

  /** A file of a model job reached a phase: checking, downloading, retrying, verifying, placing, or failed (with its failure). Sent by the engine. */
  export interface ModelFileEvent extends Partial<Failure> {
    event: "model.file";
    job: string;
    what?: string;
    node?: string | null;
    file: string | null;
    sha256: string;
    phase: string;
    bytes?: number;
    from?: number;
    total?: number | null;
    attempt?: number;
    wait_s?: number;
    why?: string;
  }

  /** A file of a model job, in bytes, throttled. Sent by the engine. */
  export interface ModelProgressEvent {
    event: "model.progress";
    job: string;
    file: string | null;
    sha256: string;
    bytes: number;
    total: number | null;
  }

  /** A model job finished: every file's outcome, and for a verify the files it dropped. Sent by the engine. */
  export interface ModelDoneEvent {
    event: "model.done";
    job: string;
    what: string;
    node: string | null;
    files: Array<FileOutcome>;
    dropped?: Array<string>;
  }

  /** A model job ended with a failure: the first failed file's, or an engine bug. Sent by the engine. */
  export interface ModelFailedEvent extends Failure {
    event: "model.failed";
    job: string;
    what?: string;
    node?: string | null;
    files: Array<FileOutcome>;
  }

  /** A model job was stopped; a download resumes where it stopped. Sent by the engine. */
  export interface ModelStoppedEvent {
    event: "model.stopped";
    job: string;
    what: string;
    node: string | null;
    files: Array<FileOutcome>;
    message: string;
  }

  /** Every event, told apart by `event`. */
  export type Event =
    | EngineReadyEvent
    | EngineFailedEvent
    | EngineWarningEvent
    | EngineExitEvent
    | RunStartEvent
    | RunDoneEvent
    | RunFailedEvent
    | RunStoppedEvent
    | NodeCachedEvent
    | NodeFitEvent
    | NodeStartEvent
    | StageEvent
    | ProgressEvent
    | CeilingEvent
    | NodeStepOomEvent
    | NodeDoneEvent
    | NodeOomEvent
    | NodeFailedEvent
    | NodeSkippedEvent
    | RuntimeStartEvent
    | RuntimeStepEvent
    | RuntimeLogEvent
    | RuntimeProgressEvent
    | RuntimeDoneEvent
    | RuntimeFailedEvent
    | RuntimeStoppedEvent
    | ModelStartEvent
    | ModelFileEvent
    | ModelProgressEvent
    | ModelDoneEvent
    | ModelFailedEvent
    | ModelStoppedEvent;

  export type EventName = Event["event"];

  /** Every method, its params and its answer. */
  export interface Methods {
    /** What the engine is. */
    "engine.hello": {
      params: { [key: string]: never };
      answer: Hello;
    };
    /** Every node the engine found, and the problems of those it could not read, by file. Queued, so a stop may answer it {stopped: true}. */
    "nodes.list": {
      params: { [key: string]: never };
      answer: {
        nodes: Array<Manifest>;
        problems: { [key: string]: Array<string> };
      } | Stopped;
    };
    /** Look for nodes again; what nodes.list answers. Queued, so a stop may answer it {stopped: true}. */
    "nodes.reload": {
      params: { [key: string]: never };
      answer: {
        nodes: Array<Manifest>;
        problems: { [key: string]: Array<string> };
      } | Stopped;
    };
    /** The fit a node would get on this machine now, at these params and for inputs of this meta. Queued, so a stop may answer it {stopped: true}. */
    "nodes.fit": {
      params: {
        node: string;
        params?: { [key: string]: unknown };
        inputs?: { [key: string]: { [key: string]: unknown } };
      };
      answer: Fit & {
        node: string;
        settings: Settings;
      } | Stopped;
    };
    /** Clear what this machine learned about a node; how many entries went. Queued, so a stop may answer it {stopped: true}. */
    "nodes.forget": {
      params: {
        node: string;
      };
      answer: {
        node: string | null;
        dropped: number;
      } | Stopped;
    };
    /** The port types, by name. Queued, so a stop may answer it {stopped: true}. */
    "ports.list": {
      params: { [key: string]: never };
      answer: {
        types: { [key: string]: PortType };
      } | Stopped;
    };
    /** A graph's plan: its steps in order, the converters inserted, and the edges checked only once their value arrives. Queued, so a stop may answer it {stopped: true}. */
    "graph.validate": {
      params: {
        graph: { [key: string]: unknown };
      };
      answer: {
        order: Array<string>;
        notes: Array<string>;
        depends: Array<Edge>;
      } | Stopped;
    };
    /** What a port of a graph's node could connect to, in the graph and by adding a node. Queued, so a stop may answer it {stopped: true}. */
    "graph.fits": {
      params: {
        graph: { [key: string]: unknown };
        port: string;
      };
      answer: {
        port: string;
        graph: Array<Connection>;
        add: Array<Connection>;
      } | Stopped;
    };
    /** Run a graph; its id at once, and its progress as run and node events. Queued, so a stop may answer it {stopped: true}. */
    "graph.run": {
      params: {
        graph: { [key: string]: unknown };
      };
      answer: {
        run: string;
      } | Stopped;
    };
    /** Every graph that ships and every one the person saved, and each broken file. Queued, so a stop may answer it {stopped: true}. */
    "graphs.list": {
      params: { [key: string]: never };
      answer: {
        graphs: Array<GraphSummary>;
        problems: { [key: string]: Array<string> };
      } | Stopped;
    };
    /** A graph by name. Queued, so a stop may answer it {stopped: true}. */
    "graphs.get": {
      params: {
        name: string;
      };
      answer: GraphSummary & {
        graph: { [key: string]: unknown };
      } | Stopped;
    };
    /** Save the person's graph by name, under the data root. Queued, so a stop may answer it {stopped: true}. */
    "graphs.save": {
      params: {
        name: string;
        graph: { [key: string]: unknown };
      };
      answer: GraphSummary | Stopped;
    };
    /** Delete the person's own graph by name; the shipped one of that name, if any, is then the one listed. Queued, so a stop may answer it {stopped: true}. */
    "graphs.remove": {
      params: {
        name: string;
      };
      answer: {
        name: string;
        removed: boolean;
        now: GraphSummary | null;
      } | Stopped;
    };
    /** Copy a file into the data root's inputs, by its content; its id and its name. Only the main process asks it. Queued, so a stop may answer it {stopped: true}. */
    "inputs.import": {
      params: { [key: string]: never };
      answer: {
        id: string;
        name: string;
      } | Stopped;
    };
    /** The file of a value a run committed, for the value protocol. Only the main process asks it. */
    "values.locate": {
      params: {
        key: string;
        port: string;
      };
      answer: {
        type: string;
        carrier: string | null;
      };
    };
    /** Stop the running graph (this one, when it names a run). */
    "run.stop": {
      params: {
        run?: string;
      };
      answer: {
        stopping: boolean;
        run?: string;
      };
    };
    /** Every runtime, its state and plan on this machine, the problems of those that cannot be read, and the machine. Queued, so a stop may answer it {stopped: true}. */
    "runtimes.list": {
      params: { [key: string]: never };
      answer: {
        runtimes: Array<Runtime>;
        problems: { [key: string]: Array<string> };
        profile: { [key: string]: unknown };
      } | Stopped;
    };
    /** The build each runtime, or this one, would install on this machine. Queued, so a stop may answer it {stopped: true}. */
    "runtimes.plan": {
      params: {
        runtime?: string;
        refresh?: boolean;
      };
      answer: {
        plans: Array<Plan>;
        profile: { [key: string]: unknown };
      } | Stopped;
    };
    /** Install a runtime; the build at once, and its progress as runtime events, or already when it is installed. Queued, so a stop may answer it {stopped: true}. */
    "runtimes.install": {
      params: {
        runtime: string;
        build?: string;
      };
      answer: {
        runtime: string;
        build?: string;
        already?: boolean;
      } | Stopped;
    };
    /** Stop a runtime's install. */
    "runtimes.stop": {
      params: {
        runtime: string;
      };
      answer: {
        stopping: boolean;
      };
    };
    /** Delete a runtime's folder under the data root. Queued, so a stop may answer it {stopped: true}. */
    "runtimes.remove": {
      params: {
        runtime: string;
      };
      answer: {
        runtime: string;
        removed: string | null;
      } | Stopped;
    };
    /** Fetch a node's files, those of these checkpoints or all; the job at once, and its progress as model events. Queued, so a stop may answer it {stopped: true}. */
    "models.download": {
      params: {
        node: string;
        /** a list of checkpoints, or "all" */
        checkpoints?: Array<string> | string;
      };
      answer: {
        job: string;
        files: Array<FileOutcome>;
      } | Stopped;
    };
    /** Hash again what the store holds of a node, or all of it; the job at once. Queued, so a stop may answer it {stopped: true}. */
    "models.verify": {
      params: {
        node?: string;
      };
      answer: {
        job: string;
        files: Array<FileOutcome>;
      } | Stopped;
    };
    /** Stop the model job (this one, when it names a job). */
    "models.stop": {
      params: {
        job?: string;
      };
      answer: {
        stopping: boolean;
        job?: string;
      };
    };
    /** What the store holds for each node, and what no node pins, without the network. Queued, so a stop may answer it {stopped: true}. */
    "models.list": {
      params: { [key: string]: never };
      answer: {
        store: string;
        free_bytes: number;
        nodes: Array<{ [key: string]: unknown }>;
        unpinned: { [key: string]: unknown };
        problems: { [key: string]: Array<string> };
      } | Stopped;
    };
    /** Delete a node's files, or a checkpoint's, or exactly the unpinned files models.list showed. Queued, so a stop may answer it {stopped: true}. */
    "models.remove": {
      params: {
        node?: string;
        checkpoint?: string;
        unpinned?: Array<string>;
      };
      answer: {
        node?: string;
        checkpoint?: string | null;
        kept?: Array<{ [key: string]: unknown }>;
        unpinned?: Array<string>;
        removed: Array<{ [key: string]: unknown }>;
        failed: Array<{ [key: string]: unknown }>;
        freed: number;
      } | Stopped;
    };
    /** The system's open dialog, then the engine's import of the photo chosen: its id and name, or null when none was chosen. Answered by the app. */
    "photo.pick": {
      params: { [key: string]: never };
      answer: {
        id: string;
        name: string;
      } | null;
    };
  }

  export type MethodName = keyof Methods;

  export type Params<M extends MethodName> = Methods[M]["params"];

  export type Answer<M extends MethodName> = Methods[M]["answer"];
}
