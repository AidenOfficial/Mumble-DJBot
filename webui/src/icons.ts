// FontAwesome(SVG,按需打包):全站图标统一走这里,不再用 emoji / 特殊符号——
// 那些字符在不同系统、字体下长得不一样(有的是彩色 emoji,有的缺字)。
// 模板里写 <Icon icon="heart" />;空心版本写 :icon="['far', 'heart']"。
import { config, library } from '@fortawesome/fontawesome-svg-core'
import '@fortawesome/fontawesome-svg-core/styles.css'
import {
  faAngleLeft, faAngleRight, faArrowTurnUp, faCaretRight, faCheck, faChevronDown, faChevronRight,
  faCircle, faCircleHalfStroke, faCopy, faDownload, faEarDeaf, faHashtag, faServer, faFileImport, faFloppyDisk, faForwardStep,
  faGlobe, faGripVertical, faMagnifyingGlass, faUpload, faHeadphones, faHeart, faListUl, faLock, faMoon, faMusic, faPause,
  faPen, faPlay, faPlus, faShuffle, faStar, faSun, faThumbtack, faTrashCan, faUser,
  faVolumeLow, faVolumeXmark, faXmark,
} from '@fortawesome/free-solid-svg-icons'
import { faHeart as farHeart, faStar as farStar } from '@fortawesome/free-regular-svg-icons'

// 样式表已经在上面静态引入,不要再往 <head> 里动态插一份
config.autoAddCss = false

library.add(
  faAngleLeft, faAngleRight, faArrowTurnUp, faCaretRight, faCheck, faChevronDown, faChevronRight,
  faCircle, faCircleHalfStroke, faCopy, faDownload, faEarDeaf, faHashtag, faServer, faFileImport, faFloppyDisk, faForwardStep,
  faGlobe, faGripVertical, faMagnifyingGlass, faUpload, faHeadphones, faHeart, faListUl, faLock, faMoon, faMusic, faPause,
  faPen, faPlay, faPlus, faShuffle, faStar, faSun, faThumbtack, faTrashCan, faUser,
  faVolumeLow, faVolumeXmark, faXmark,
  farHeart, farStar,
)

export { FontAwesomeIcon as Icon } from '@fortawesome/vue-fontawesome'
