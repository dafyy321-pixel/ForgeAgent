import type { ReactNode, SVGProps } from 'react';

// Forge Marks: 24-unit engineering symbols. See docs/Forge-Marks-图标设计与来源.md.
export type IconProps = SVGProps<SVGSVGElement> & { size?: number | string };
function mark(name: string, drawing: ReactNode) {
 const Icon = ({size=24, className='', ...props}:IconProps) => <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="square" strokeLinejoin="miter" aria-hidden="true" focusable="false" className={`forge-mark ${className}`} {...props}>{drawing}</svg>;
 Icon.displayName = name;
 return Icon;
}
const ink = (d:string) => <path d={d} className="mark-ink" fill="currentColor" stroke="none" opacity=".16"/>;
const cut = 'M7 3H17L21 7V17L17 21H7L3 17V7Z';
const sheet = 'M5 3H15L20 8V21H5Z';

export const ForgeMark = mark('ForgeMark', <>{ink('M3 5H21L17 10H11V14H16L13 18H6V10L3 8Z')}<path d="M3 5H21L17 10H11V14H16L13 18H6V10L3 8ZM4 21H17M17 2V3"/><path d="M7 7H15"/></>);
export const Home = mark('Home', <>{ink('M4 4H14V13H4Z')}<path d="M4 13V4H14V9H20V20H4V17M4 13H10V20M14 9V14H20M7 7H10"/></>);
export const LayoutList = mark('LayoutList', <>{ink('M4 3H16L20 7H4Z')}<path d="M4 3H16L20 7V10L18 12L20 14V21H4V14L6 12L4 10ZM8 8H14M8 12H14M8 16H16"/></>);
export const Inbox = mark('Inbox', <>{ink('M3 14H8L10 17H14L16 14H21V21H3Z')}<path d="M3 14L6 7H9M15 7H18L21 14V21H3V14H8L10 17H14L16 14H21M12 3V12M9 9L12 12L15 9"/></>);
export const FolderGit2 = mark('FolderGit2', <>{ink('M3 8L12 12L21 8L12 4Z')}<path d="M3 8L12 4L21 8V18L12 22L3 18ZM3 8L12 12L21 8M12 12V22M8 6L17 10V14"/></>);
export const Project = mark('Project', <>{ink('M3 4H9L12 8H21V11H3Z')}<path d="M3 4H9L12 8H21V21H3ZM3 11H21M7 15H11M7 18H15"/></>);
export const BookOpen = mark('BookOpen', <>{ink('M3 4H9L12 7V21L9 18H3Z')}<path d="M3 4H9L12 7L15 4H21V18H15L12 21L9 18H3ZM12 7V21M6 8H8M6 11H8M16 8H18M16 11H18M18 4V2"/></>);
export const Activity = mark('Activity', <>{ink('M3 15H7L10 9L14 16L18 6H21V21H3Z')}<path d="M3 3V21H21M3 15H7L10 9L14 16L18 6H21M7 4H10"/></>);
export const ShieldCheck = mark('ShieldCheck', <>{ink('M12 2L20 5V13L17 18L12 22L7 18L4 13V5Z')}<path d="M12 2L20 5V13L17 18L12 22L7 18L4 13V5ZM8 11L11 14L16 9M9 4H15"/></>);
export const Shield = mark('Shield', <>{ink('M12 3V21L5 16V6Z')}<path d="M12 3L20 6V14L17 18L12 21L7 18L4 14V6ZM12 7V15"/></>);
export const Workflow = mark('Workflow', <>{ink('M3 3H10V10H3ZM14 14H21V21H14Z')}<path d="M3 3H10V10H3ZM14 14H21V21H14ZM10 6H18V10M14 18H6V14"/></>);
export const Settings = mark('Settings', <>{ink('M3 4H21V20H3Z')}<path d="M3 4H21V20H3ZM7 8H17M7 15H17M10 6V10M14 13V17"/></>);
export const Bell = mark('Bell', <>{ink('M5 16L7 12V7L10 4H14L17 7V12L19 16Z')}<path d="M5 16L7 12V7L10 4H14L17 7V12L19 16ZM10 20H14M12 2V4M3 8V5M21 8V5"/></>);
export const Terminal = mark('Terminal', <>{ink('M3 3H17L21 7H3Z')}<path d="M3 3H17L21 7V21H3ZM3 7H21M7 11L10 14L7 17M13 17H17"/></>);
export const Layers3 = mark('Layers3', <>{ink('M3 7L12 3L21 7L12 11Z')}<path d="M3 7L12 3L21 7L12 11ZM3 12L12 16L21 12M3 17L12 21L21 17"/></>);
export const Database = mark('Database', <>{ink('M4 3H20V8H4Z')}<path d="M4 3H20V21H4ZM4 9H20M4 15H20M7 6H9M7 12H9M7 18H9M16 6H17M16 12H17M16 18H17"/></>);
export const Network = mark('Network', <>{ink('M9 3H15V9H9ZM2 16H8V22H2ZM16 16H22V22H16Z')}<path d="M9 3H15V9H9ZM2 16H8V22H2ZM16 16H22V22H16ZM12 9V12M5 16V12H19V16"/></>);
export const FlaskConical = mark('FlaskConical', <>{ink('M6 14H18L21 19L19 21H5L3 19Z')}<path d="M8 3H16M10 3V9L3 19L5 21H19L21 19L14 9V3M7 14H17M9 18H11"/></>);
export const FileText = mark('FileText', <>{ink('M15 3L20 8H15Z')}<path d={`${sheet}M15 3V8H20M8 12H16M8 16H14`}/></>);
export const FileCode2 = mark('FileCode2', <>{ink('M15 3L20 8H15Z')}<path d={`${sheet}M15 3V8H20M10 11L7 14L10 17M15 11L18 14L15 17`}/></>);
export const FileCheck2 = mark('FileCheck2', <>{ink('M15 3L20 8H15Z')}<path d={`${sheet}M15 3V8H20M8 14L11 17L17 11`}/></>);
export const GitBranch = mark('GitBranch', <>{ink('M3 3H9V9H3ZM15 3H21V9H15ZM3 15H9V21H3Z')}<path d="M3 3H9V9H3ZM15 3H21V9H15ZM3 15H9V21H3ZM6 9V15M18 9V12H6"/></>);
export const CheckCircle2 = mark('CheckCircle2', <>{ink(cut)}<path d={`${cut}M7 12L11 16L17 9`}/></>);
export const XCircle = mark('XCircle', <>{ink(cut)}<path d={`${cut}M9 9L15 15M15 9L9 15`}/></>);
export const TriangleAlert = mark('TriangleAlert', <>{ink('M10 3H14L22 19V21H2V19Z')}<path d="M10 3H14L22 19V21H2V19ZM12 8V13M12 17V18"/></>);
export const Info = mark('Info', <>{ink(cut)}<path d={`${cut}M10 11H12V17M10 17H14M12 7V8`}/></>);
export const Clock3 = mark('Clock3', <>{ink('M12 3V12L17 15L20 17L21 7L17 3Z')}<path d={`${cut}M12 7V12L16 14`}/></>);
export const Clock = Clock3;
export const LoaderCircle = mark('LoaderCircle', <><path d="M12 3H17L21 7V12M21 17L17 21H12M7 21L3 17V12M3 7L7 3"/><path d="M12 3H17L21 7" strokeWidth="2.8"/></>);
export const Circle = mark('Circle', <path d="M8 5H16L19 8V16L16 19H8L5 16V8Z"/>);
export const Bookmark = mark('Bookmark', <>{ink('M5 3H19V8H5Z')}<path d="M5 3H19V21L12 17L5 21ZM8 7H16"/></>);
export const Copy = mark('Copy', <>{ink('M8 8H17L21 12H8Z')}<path d="M8 8H17L21 12V21H8ZM4 16H3V3H16V4"/></>);
export const Trash2 = mark('Trash2', <>{ink('M6 7H18L17 21H7Z')}<path d="M3 7H21M6 7L7 21H17L18 7M8 7V3H16V7M10 11V17M14 11V17"/></>);
export const Code2 = mark('Code2', <path d="M7 6L2 12L7 18M17 6L22 12L17 18M14 3L10 21"/>);
export const Sparkles = mark('Sparkles', <>{ink('M10 4L12 10L18 12L12 14L10 20L8 14L2 12L8 10Z')}<path d="M10 4L12 10L18 12L12 14L10 20L8 14L2 12L8 10ZM19 3V7M17 5H21M20 17V21M18 19H22"/></>);
export const Zap = mark('Zap', <>{ink('M13 2L4 13H11L9 22L21 9H13Z')}<path d="M13 2L4 13H11L9 22L21 9H13Z"/></>);
// Adapted from Iconoir (MIT), pinned source paths and exact license in public/licenses.
export const ArrowRight = mark('ArrowRight', <path d="M3 12H21M21 12L12.5 3.5M21 12L12.5 20.5"/>);
export const ArrowUpRight = mark('ArrowUpRight', <path d="M6.00005 19L19 5.99996M19 5.99996V18.48M19 5.99996H6.52005"/>);
export const X = mark('X', <path d="M6.75827 17.2426L12.0009 12M17.2435 6.75736L12.0009 12M12.0009 12L6.75827 6.75736M12.0009 12L17.2435 17.2426"/>);
export const Search = mark('Search', <>{ink('M7 3H15L19 7V15L15 19H7L3 15V7Z')}<path d="M17 17L21 21M7 3H15L19 7V15L15 19H7L3 15V7ZM7 7H10"/></>);
export const ArrowLeft = mark('ArrowLeft', <path d="M21 12H3M3 12L11.5 3.5M3 12L11.5 20.5"/>);
export const ArrowDownToLine = mark('ArrowDownToLine', <path d="M12 3V15M7 10L12 15L17 10M4 17V21H20V17"/>);
export const ChevronRight = mark('ChevronRight', <path d="M9 6L15 12L9 18"/>);
export const ChevronDown = mark('ChevronDown', <path d="M6 9L12 15L18 9"/>);
export const Plus = mark('Plus', <path d="M12 4V20M4 12H20"/>);
export const Check = mark('Check', <path d="M4 12L9 17L20 6"/>);
export const CheckCheck = mark('CheckCheck', <path d="M2 12L7 17L17 6M12 17L22 6"/>);
export const Play = mark('Play', <>{ink('M6 3L21 12L6 21Z')}<path d="M6 3L21 12L6 21Z"/></>);
export const Pause = mark('Pause', <>{ink('M5 4H9V20H5ZM15 4H19V20H15Z')}<path d="M5 4H9V20H5ZM15 4H19V20H15Z"/></>);
export const Square = mark('Square', <>{ink('M5 4H16L20 8V20H4V5Z')}<path d="M4 4H16L20 8V20H4Z"/></>);
export const RotateCcw = mark('RotateCcw', <path d="M4 4V10H10M4 10L8 4H17L21 8V16L17 20H8L5 17"/>);
export const SlidersHorizontal = mark('SlidersHorizontal', <><path d="M3 6H8M12 6H21M3 18H14M18 18H21M8 3H12V9H8ZM14 15H18V21H14Z"/>{ink('M8 3H12V9H8ZM14 15H18V21H14Z')}</>);
export const Filter = mark('Filter', <>{ink('M3 4H21L14 12H10Z')}<path d="M3 4H21L14 12V19L10 21V12Z"/></>);
export const ListFilter = mark('ListFilter', <path d="M3 5H21M3 12H15M3 19H9"/>);
export const MoreHorizontal = mark('MoreHorizontal', <path d="M3 11H5V13H3ZM11 11H13V13H11ZM19 11H21V13H19Z" fill="currentColor" stroke="none"/>);
export const Menu = mark('Menu', <path d="M3 5H21M3 12H15M3 19H21"/>);
export const PanelRightClose = mark('PanelRightClose', <>{ink('M16 3H21V21H16Z')}<path d="M3 3H21V21H3ZM16 3V21M7 9L10 12L7 15"/></>);
export const PanelRightOpen = mark('PanelRightOpen', <>{ink('M16 3H21V21H16Z')}<path d="M3 3H21V21H3ZM16 3V21M10 9L7 12L10 15"/></>);
export const Command = mark('Command', <path d="M8 8H5V4H8V20H4V16H20V20H16V4H20V8Z"/>);
// A literal git repository connector rather than a modified third-party brand logo.
export const Github = mark('Github', <>{ink('M12 2L22 12L12 22L2 12Z')}<path d="M12 2L22 12L12 22L2 12ZM8 6V13L12 17M8 9H13L16 12"/></>);
