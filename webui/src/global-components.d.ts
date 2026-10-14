// 全局注册的组件(main.ts),给 vue-tsc 的模板类型检查用
import type { FontAwesomeIcon } from '@fortawesome/vue-fontawesome'

declare module 'vue' {
  interface GlobalComponents {
    Icon: typeof FontAwesomeIcon
  }
}

export {}
