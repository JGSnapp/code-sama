import type { SceneEvent } from "./events.js";

type Listener<T> = (event: T) => void | Promise<void>;

export class TypedEventBus<TEvent extends { type: string }> {
  private listeners = new Map<TEvent["type"], Set<Listener<TEvent>>>();
  private allListeners = new Set<Listener<TEvent>>();

  on<TType extends TEvent["type"]>(type: TType, listener: Listener<Extract<TEvent, { type: TType }>>): () => void {
    const set = this.listeners.get(type) ?? new Set();
    set.add(listener as Listener<TEvent>);
    this.listeners.set(type, set);
    return () => set.delete(listener as Listener<TEvent>);
  }

  onAny(listener: Listener<TEvent>): () => void {
    this.allListeners.add(listener);
    return () => this.allListeners.delete(listener);
  }

  async emit(event: TEvent): Promise<void> {
    const typed = this.listeners.get(event.type) ?? new Set();
    await Promise.all([...typed, ...this.allListeners].map((listener) => listener(event)));
  }
}

export type SceneEventBus = TypedEventBus<SceneEvent>;
