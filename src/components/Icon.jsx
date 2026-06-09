/* =============================================================
   components/Icon.jsx — thin wrapper over lucide-react that keeps
   the prototype's <Icon name="..." /> ergonomics. Design system
   §14.5 specifies Lucide line icons.
   ============================================================= */
import {
  Sparkles, Shirt, Image, ImagePlus, LayoutGrid, Type, Shapes, Eye, EyeOff,
  Save, Download, Upload, Undo2, Redo2, Plus, PlusCircle, Wand2, RefreshCw,
  PersonStanding, Smile, Trash2, CheckSquare, Circle, ChevronUp, ChevronDown,
  ChevronLeft, ChevronRight, X, Check, ArrowRight, ArrowUp, ArrowLeft, Layers,
  BringToFront, SendToBack, Settings, User, Library, TriangleAlert, CircleAlert,
  LoaderCircle, Lock, Unlock, Pencil, Copy, GripVertical, Info, Star, Move,
  AlignLeft, AlignCenter, AlignRight, Bold, Italic, Underline, Strikethrough,
  Crop, RotateCw, Minus, Maximize, Search, Clock, Coins, Link, Unlink, List,
  ListOrdered, Droplet, Ban, HelpCircle,
} from 'lucide-react';

const MAP = {
  sparkles: Sparkles, shirt: Shirt, image: Image, imagePlus: ImagePlus,
  layout: LayoutGrid, grid: LayoutGrid, type: Type, shapes: Shapes, eye: Eye,
  eyeOff: EyeOff, save: Save, download: Download, upload: Upload, undo: Undo2,
  redo: Redo2, plus: Plus, plusCircle: PlusCircle, wand: Wand2, refresh: RefreshCw,
  landscape: Image, person: PersonStanding, smile: Smile, trash: Trash2,
  checkSquare: CheckSquare, circle: Circle, chevUp: ChevronUp, chevDown: ChevronDown,
  chevLeft: ChevronLeft, chevRight: ChevronRight, x: X, check: Check,
  arrowRight: ArrowRight, arrowUp: ArrowUp, arrowLeft: ArrowLeft, layers: Layers,
  bringFront: BringToFront, sendBack: SendToBack, settings: Settings, user: User,
  library: Library, alertTri: TriangleAlert, alertCircle: CircleAlert,
  loader: LoaderCircle, lock: Lock, unlock: Unlock, pencil: Pencil, copy: Copy,
  gripV: GripVertical, info: Info, star: Star, move: Move, alignLeft: AlignLeft,
  alignCenter: AlignCenter, alignRight: AlignRight, bold: Bold, italic: Italic,
  underline: Underline, strike: Strikethrough, crop: Crop, rotate: RotateCw,
  minus: Minus, maximize: Maximize, search: Search, clock: Clock, coins: Coins,
  link: Link, unlink: Unlink, listBullet: List, listOrdered: ListOrdered,
  droplet: Droplet, ban: Ban,
};

export function Icon({ name, size = 20, stroke = 2, fill = 'none', className, style }) {
  const C = MAP[name] || HelpCircle;
  return <C size={size} strokeWidth={stroke} fill={fill} className={className} style={style} aria-hidden="true" />;
}

export default Icon;
