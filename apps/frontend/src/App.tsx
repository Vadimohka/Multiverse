import {useState} from 'react';
import './responsive.css';
import {Navigate,NavLink,Route,Routes,useNavigate} from 'react-router-dom';
import {Activity,Blocks,BookOpen,Database,FileCheck,FileKey,FileOutput,Home,LogOut,Play,Settings,Users,Workflow as WorkflowIcon} from 'lucide-react';
import {ApiTokensPage,AuditPage,DashboardPage,DataPage,ExportsPage,LoginPage,PlaceholderPage,ProjectsPage,PromptsPage,ReviewPage,RunsPage,SchedulesPage,SchemasPage,SettingsPage,SourcesPage,UsersPage,WorkflowEditorPage,WorkflowTemplatesPage,WorkflowTransferPage,WorkflowsPage} from './pages';
import {SourcePresetStudioPage} from './preset-studio';

const nav=[
  ['/','Главная',Home],['/projects','Проекты',Blocks],['/sources','Источники',BookOpen],['/presets','Пресеты источников',Blocks],['/workflows','Workflows',WorkflowIcon],['/workflow-templates','Шаблоны workflow',Blocks],['/schemas','Схемы данных',Database],['/runs','Запуски',Play],['/review','Проверка данных',FileCheck],['/data','Данные',Activity],['/api-tokens','API-токены',FileKey],['/prompts','AI и промпты',Settings],['/schedules','Расписания',Activity],['/exports','Экспорт',FileOutput],['/settings','Подключения',Settings],['/users','Пользователи',Users],['/audit','Audit log',Activity],
] as const;

function Layout({onLogout}:{onLogout:()=>void}){
  const navigate=useNavigate();
  function logout(){localStorage.clear();onLogout();navigate('/login',{replace:true})}
  return <div className="shell"><aside><div className="brand">Parser Studio</div><nav>{nav.map(([to,label,Icon])=><NavLink key={to} to={to} end={to==='/' }><Icon size={18}/><span>{label}</span></NavLink>)}</nav><button className="logout" onClick={logout}><LogOut size={18}/>Выйти</button></aside><main><Routes>
    <Route path="/" element={<DashboardPage/>}/><Route path="/projects" element={<ProjectsPage/>}/><Route path="/sources" element={<SourcesPage/>}/><Route path="/presets" element={<SourcePresetStudioPage/>}/><Route path="/workflows" element={<WorkflowsPage/>}/><Route path="/workflows/:id" element={<WorkflowEditorPage/>}/><Route path="/workflow-templates" element={<WorkflowTemplatesPage/>}/><Route path="/workflow-transfer" element={<WorkflowTransferPage/>}/><Route path="/schemas" element={<SchemasPage/>}/><Route path="/runs" element={<RunsPage/>}/><Route path="/review" element={<ReviewPage/>}/><Route path="/data" element={<DataPage/>}/><Route path="/api-tokens" element={<ApiTokensPage/>}/><Route path="/prompts" element={<PromptsPage/>}/><Route path="/schedules" element={<SchedulesPage/>}/><Route path="/exports" element={<ExportsPage/>}/><Route path="/settings" element={<SettingsPage/>}/><Route path="/users" element={<UsersPage/>}/><Route path="/audit" element={<AuditPage/>}/><Route path="/login" element={<Navigate to="/" replace/>}/><Route path="*" element={<PlaceholderPage/>}/>
  </Routes></main></div>;
}

export default function App(){
  const [token,setToken]=useState(localStorage.getItem('access_token'));
  if(!token)return <Routes><Route path="/login" element={<LoginPage onLogin={()=>setToken(localStorage.getItem('access_token'))}/>}/><Route path="*" element={<Navigate to="/login"/>}/></Routes>;
  return <Layout onLogout={()=>setToken(null)}/>;
}
