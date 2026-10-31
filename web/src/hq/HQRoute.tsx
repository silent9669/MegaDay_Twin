import {Navigate,useLocation,useParams,useSearchParams} from "react-router";
export function HQRoute({tab}:{tab:string}) {
 const {hash}=useLocation();
 const {sku,id}=useParams(),[search]=useSearchParams();
 const params=new URLSearchParams(search);params.set('district','hq');params.set('hq',tab);
 if(sku){params.set('sku',sku);params.set('mode','model');}if(id)params.set('campaign',id);
 return <Navigate to={`/city-demo?${params}${hash}`} replace/>;
}
