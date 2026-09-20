"""Offline Brain-v1 training; no MT5, execution, shadow, or paper-trading dependency."""
from collections import Counter
from hashlib import sha256
import json

from ai.artifacts import ModelArtifactManager
from ai.brain_v1 import BrainV1Dataset, LABEL_SPEC_V1
from ai.model import ModelTrainer
from ai.types import LabeledObservation, ProposalAction
from config.settings import get_settings
from database.audit import AuditRepository
from database.session import initialize_database
from models.registry import ModelRegistry
from models.types import ModelMetadata, ModelStatus

HASH = "05ce1447a4251693dd548ab57007ac71558d4be6cd8f8e6e9e77ab18b6ce78f5"

def metric(model, rows, threshold):
    labels=list(ProposalAction); matrix={a:{b:0 for b in labels} for a in labels}; conf=[]; predictions=[]
    for item in rows:
        p=model.probabilities(item.features); action=max(p,key=p.get); confidence=p[action]
        if action is not ProposalAction.NO_TRADE and confidence<threshold: action=ProposalAction.NO_TRADE
        matrix[item.target][action]+=1; predictions.append(action.value); conf.append(confidence)
    per={}; recalls=[]; f1s=[]
    for label in labels:
        tp=matrix[label][label]; fp=sum(matrix[o][label] for o in labels if o!=label); fn=sum(matrix[label][o] for o in labels if o!=label)
        precision=tp/(tp+fp) if tp+fp else 0.; recall=tp/(tp+fn) if tp+fn else 0.; f1=2*precision*recall/(precision+recall) if precision+recall else 0.
        per[label.value]={"precision":precision,"recall":recall,"f1":f1}; recalls.append(recall); f1s.append(f1)
    return {"balanced_accuracy":sum(recalls)/3,"macro_f1":sum(f1s)/3,"per_class":per,"confusion_matrix":{a.value:{b.value:n for b,n in r.items()} for a,r in matrix.items()},"prediction_distribution":dict(Counter(predictions)),"confidence":{"min":min(conf),"max":max(conf),"mean":sum(conf)/len(conf)}}

def main():
    settings=get_settings(); sessions=initialize_database(settings); rows,names,meta=BrainV1Dataset(sessions,HASH).rows()
    groups={name:tuple(LabeledObservation(r['timestamp'],r['raw_data_cutoff'],r['feature_snapshot_id'],r['feature_version'],r['features'],ProposalAction(r['target']),r['target_timestamp']) for r in rows if r['split']==name) for name in ('TRAIN','VALIDATION','OOS')}
    if tuple(map(len,(groups['TRAIN'],groups['VALIDATION'],groups['OOS']))) != (20330,4352,4352): raise RuntimeError('Label accounting gate failed.')
    artifact=ModelArtifactManager()
    try:
        model, _ = artifact.load('Brain-v1')
    except FileNotFoundError:
        model=ModelTrainer().train(groups['TRAIN'],names,'Brain-v1',epochs=25,learning_rate=.08)
    threshold=max((.40,.45,.50,.55,.60),key=lambda x:metric(model,groups['VALIDATION'],x)['macro_f1'])
    validation=metric(model,groups['VALIDATION'],threshold); oos=metric(model,groups['OOS'],threshold)
    importance=sorted(((name,sum(abs(w[i]) for w in model.weights)) for i,name in enumerate(names)),key=lambda x:x[1],reverse=True)
    path=artifact.directory / 'Brain-v1.json'
    if not path.exists(): path=artifact.save(model,{"dataset_version":"research-dataset-v1","dataset_hash":HASH,"feature_set_version":"feature-set-v1","label_spec":LABEL_SPEC_V1,"confidence_policy":{"version":"confidence-policy-v1","threshold":threshold},"validation":validation,"oos":oos,"feature_importance":importance,"seed":0})
    digest=sha256(path.read_bytes()).hexdigest(); registry=ModelRegistry(sessions,audit=AuditRepository(sessions))
    metadata=ModelMetadata('Brain-v1',None,{"dataset_version":"research-dataset-v1","dataset_hash":HASH,"train_rows":20330,"validation_rows":4352,"oos_rows":4352,"feature_names":list(names)}, {},"feature-set-v1","interpretable_multinomial_logistic",{"epochs":25,"learning_rate":.08,"random_seed":0})
    try: registry.get('Brain-v1')
    except KeyError: registry.register_training(metadata)
    safe_accounting={"class_distribution":meta["class_distribution"],"exclusions":meta["exclusions"],"dataset_id":meta["manifest"].dataset_id,"dataset_hash":meta["manifest"].content_hash}
    registry.update_training_metadata('Brain-v1',label_spec=LABEL_SPEC_V1,confidence_policy={"version":"confidence-policy-v1","threshold":threshold},validation_metrics=validation,out_of_sample_metrics=oos,artifact_path=str(path),artifact_hash=digest,feature_importance=importance,label_accounting=safe_accounting)
    if registry.get('Brain-v1')['status'] == 'TRAINING': registry.transition('Brain-v1',ModelStatus.CANDIDATE)
    print(json.dumps({"threshold":threshold,"validation":validation,"oos":oos,"artifact":str(path),"artifact_hash":digest,"top_features":importance[:10]},default=str))
if __name__=='__main__': main()
