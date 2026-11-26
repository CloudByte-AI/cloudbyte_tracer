/** LangChain callbacks track spans without activating them in OTel context. */
export function contextProvider(
  provider: any,
  api: any,
  parents: Map<string, string | undefined>,
  requireOptional: NodeRequire,
) {
  const resolve = (ctx: any) => {
    try {
      const { AsyncLocalStorageProviderSingleton } = requireOptional('@langchain/core/singletons');
      const config = AsyncLocalStorageProviderSingleton.getRunnableConfig();
      const callbacks = config?.callbacks;
      const handlers = [...(callbacks?.handlers ?? []), ...(callbacks?.inheritableHandlers ?? [])];
      const handler = handlers.find((h: any) => h.name === 'OpenInferenceLangChainTracer');
      const candidate = handler?.runs?.[callbacks?.getParentRunId?.()]?.span;
      if (!candidate) return ctx;
      const current = api.trace.getSpanContext(ctx),
        seen = new Set<string>();
      let cursor: string | undefined = candidate.spanContext().spanId;
      while (cursor && !seen.has(cursor)) {
        if (cursor === current?.spanId) return api.trace.setSpan(ctx, candidate);
        seen.add(cursor);
        cursor = parents.get(cursor);
      }
    } catch {}
    return ctx;
  };
  return {
    getTracer: (...args: any[]) => {
      const tracer = provider.getTracer(...args);
      return new Proxy(tracer, {
        get(target, key) {
          if (key === 'startSpan')
            return (name: string, options: any, ctx: any = api.context.active()) =>
              target.startSpan(name, options, resolve(ctx));
          if (key === 'startActiveSpan')
            return (name: string, ...rest: any[]) => {
              const fn = rest.pop(),
                options = rest.shift() ?? {},
                ctx = rest.shift() ?? api.context.active();
              return target.startActiveSpan(name, options, resolve(ctx), fn);
            };
          const value = target[key];
          return typeof value === 'function' ? value.bind(target) : value;
        },
      });
    },
  };
}
