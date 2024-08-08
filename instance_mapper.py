import re
import json
import boto3
from botocore.config import Config

# TODO?
# maintain list of rough grav comps
#   eg: 1xm7a.16xl = {'instance': m7g.16xl, 'count': 1}
#   eg: 1xm7a.32xlarge = {'instance': m7g.16xl, 'count': 2}

#get all instances groups by cpu mfg(amd, intel, aws)
def get_instances_by_cpu_mfg():
    amd_instances = [x["InstanceType"] for x in ec2.get_instance_types_from_instance_requirements(
        ArchitectureTypes=['i386','x86_64','arm64','x86_64_mac','arm64_mac'],
        VirtualizationTypes=["hvm","paravirtual"],
        InstanceRequirements={
            "BareMetal":"included",
            "VCpuCount":{"Min":0},
            "MemoryMiB":{"Min":0},
            "CpuManufacturers": ["amd"],
            "BurstablePerformance": "included"
        })["InstanceTypes"]]
    
    intel_instances = [x["InstanceType"] for x in ec2.get_instance_types_from_instance_requirements(
        ArchitectureTypes=['i386','x86_64','arm64','x86_64_mac','arm64_mac'],
        VirtualizationTypes=["hvm","paravirtual"],
        InstanceRequirements={
            "BareMetal":"included",
            "VCpuCount":{"Min":0},
            "MemoryMiB":{"Min":0},
            "CpuManufacturers": ["intel"],
            "BurstablePerformance": "included"
        })["InstanceTypes"]]
    
    aws_instances = [x["InstanceType"] for x in ec2.get_instance_types_from_instance_requirements(
        ArchitectureTypes=['i386','x86_64','arm64','x86_64_mac','arm64_mac'],
        VirtualizationTypes=["hvm","paravirtual"],
        InstanceRequirements={
            "BareMetal":"included",
            "VCpuCount":{"Min":0},
            "MemoryMiB":{"Min":0},
            "CpuManufacturers": ["amazon-web-services"],
            "BurstablePerformance": "included"
        })["InstanceTypes"]]
    
    return {
        "amd": amd_instances, 
        "intel": intel_instances,
        "aws": aws_instances
    }

# get all instances and raw metadata
def get_all_instance_descriptions():
    all_instances = []
    
    for page in ec2.get_paginator('describe_instance_types').paginate():
        all_instances.extend(page["InstanceTypes"])
    
    return all_instances

# go back and fill in instance generational data
# couldn't figure out how to cleanly/efficiently fill it in during main loop
def backfill_generation_data():
  for x in instances:
    # i tried to do this inline in the main loop but it became unreadable. lots of nested conditions.
    x['latest_family_gen'] = max([y['generation_number'] for y in instances if y['family'] == x['family']], default=0)
    x['latest_graviton_gen'] = max([y['generation_number'] for y in instances if y['family'] == x['family'] and y['processor'] == 'aws'], default=0)
    x['latest_amd_gen'] = max([y['generation_number'] for y in instances if y['family'] == x['family'] and y['processor'] == 'amd'], default=0)
    x['latest_intel_gen']= max([y['generation_number'] for y in instances if y['family'] == x['family'] and y['processor'] == 'intel'], default=0)
    
    if x['generation_number'] == x['latest_family_gen']:
      x['generation'] = 'latest'
    elif x['generation_number'] == x['latest_family_gen']-1:
      x['generation'] = 'preceding'
    else:
      x['generation'] = 'previous'

# find 1:1 instance comps based on vcpu and mem. _rough_ adaptation of original method, doesn't use hypervisor/metal matching
def find_comparable_instances(max_vcpu_factor=1, max_mem_factor=1, force_hypervisor_matching=False, force_bare_metal_matching=False):
   for inst in instances:
     for comp in instances:
       if comp == inst:
         continue
       
       if comp['family'] == inst['family']:
        if comp['generation_number'] >= inst['generation_number']:
          # TODO: capture instances that don't have a 1:1(eg: i3en.3xlarge ~= i4i.4xlarge)
          # check for 1:1 vcpu matching, validate the cpu mfg and that this is the latest generation
          if (comp['vcpus']/inst['vcpus']==max_vcpu_factor):
             if comp['processor'] == 'aws' and comp['generation_number'] == comp['latest_graviton_gen']:
               inst['graviton_vcpu_comps'].append(comp['name'])
             elif comp['processor'] == 'amd' and comp['generation_number'] == comp['latest_amd_gen']:
               inst['amd_vcpu_comps'].append(comp['name'])
             elif comp['processor'] == 'intel' and comp['generation_number'] == comp['latest_intel_gen']:
               inst['intel_vcpu_comps'].append(comp['name'])

          # skip if 0 mem. these are shared mem instances(eg: t2.micro)
          if (comp['mem'] != 0 and inst['mem'] != 0):
            if (comp['mem']/inst['mem']==max_mem_factor):
             if comp['processor'] == 'aws' and comp['generation_number'] == comp['latest_graviton_gen']:
               inst['graviton_mem_comps'].append(comp['name'])
             elif comp['processor'] == 'amd' and comp['generation_number'] == comp['latest_amd_gen']:
               inst['amd_mem_comps'].append(comp['name'])
             elif comp['processor'] == 'intel' and comp['generation_number'] == comp['latest_intel_gen']:
               inst['intel_mem_comps'].append(comp['name'])

REGION = 'us-east-1'
FAMILY_DESIGNATION_REGEX = '^[a-z]+'
FAMILY_GENERATION_REGEX = '\d'
INSTANCE_FEATURES_REGEX = '(s|d|n|e|z|flex)*$'
config = Config(region_name=REGION)
ec2 = boto3.client("ec2", config=config)

instances_cpu_grouped = get_instances_by_cpu_mfg()
all_instance_descriptions = get_all_instance_descriptions()

instances = []

# main list generation loop
# go instance by instance and fill in cpu mfg, generation, family, etc
for x in all_instance_descriptions:
  for cpu_mfg in instances_cpu_grouped:
    for cpu_inst in instances_cpu_grouped[cpu_mfg]:
      if cpu_inst == x["InstanceType"]:
        # override inf1*(and other future?) instances
        # get_instance_types_from_instance_requirements returns inf1 with mfg as amazon_web_services when including x86_64 arch type but it is a xeon proc. inf2 doesn't do this
        if cpu_mfg == 'aws' and 'x86_64' in x['ProcessorInfo']['SupportedArchitectures']:
          cpu_mfg = 'intel'

        instance_class = cpu_inst.split('.')[0]
        instance_family = re.search(FAMILY_DESIGNATION_REGEX, instance_class)[0]

        if instance_class.startswith('u'):
          # if there's another 'generation' of U instances, probably have to be smarter than just slapping a 1 in there and calling it a day
          instance_generation = 1
        else:
          regex_result = re.search(FAMILY_GENERATION_REGEX, instance_class)[0]
          instance_generation = int(regex_result[0] if regex_result is not None else 1)
        
        instance_features_str = instance_class.replace(instance_family, "", 1)  # replace instance family(eg: m) with nothing, trim it
        instance_features_str = instance_features_str.replace(str(instance_generation), "", 1)  # replace instance generation(eg: 6) with nothing, trim it
        instance_features_str = re.search(INSTANCE_FEATURES_REGEX, instance_features_str)[0]
        
        if 'flex' not in instance_features_str:
            instance_features = list(instance_features_str)
        else:
            instance_features = [instance_features_str]
        
        if instance_family == None:
          print('no family somehow')
          continue
        
        instance_template = {
          'name': cpu_inst,
          'family': instance_family,
          'generation_number': instance_generation,
          'generation': '',
          'processor': cpu_mfg,
          'latest_family_gen': 0,
          'latest_graviton_gen': 0,
          'latest_intel_gen': 0,
          'latest_amd_gen': 0,
          'instance_features': instance_features,
          'vcpus': x['VCpuInfo']['DefaultVCpus'],
          'mem': round(x['MemoryInfo']['SizeInMiB']/1024),
          'instance_store_supported': x['InstanceStorageSupported'],
          'instance_store_size': x['InstanceStorageInfo']['Disks'][0]['SizeInGB'] if x['InstanceStorageSupported'] else "",
          'instance_store_count': x['InstanceStorageInfo']['Disks'][0]['Count'] if x['InstanceStorageSupported'] else "",
          'instance_store_type': x['InstanceStorageInfo']['Disks'][0]['Type'] if x['InstanceStorageSupported'] else "",
          'virtualization': x['SupportedVirtualizationTypes'],
          'metal': x['BareMetal'],
          'hypervisor': x['Hypervisor'] if 'Hypervisor' in x else "",
          'clock_speed': x['ProcessorInfo']['SustainedClockSpeedInGhz'] if 'SustainedClockSpeedInGhz' in x['ProcessorInfo'] else 0,
          'graviton_vcpu_comps': [],
          'amd_vcpu_comps': [],
          'intel_vcpu_comps': [],
          'graviton_mem_comps': [],
          'amd_mem_comps': [],
          'intel_mem_comps': []
        }

        instances.append(instance_template)

backfill_generation_data()
find_comparable_instances()

#debug output
print(json.dumps(instances, indent=4))