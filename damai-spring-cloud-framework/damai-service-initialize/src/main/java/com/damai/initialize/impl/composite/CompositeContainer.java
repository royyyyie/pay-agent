package com.damai.initialize.impl.composite;

import com.damai.enums.BaseCode;
import com.damai.exception.DaMaiFrameException;
import org.springframework.context.ConfigurableApplicationContext;

import java.util.Collection;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.TreeMap;
import java.util.stream.Collectors;
/**
 * @program: 极度真实还原大麦网高并发实战项目。 添加 阿星不是程序员 微信，添加时备注 大麦 来获取项目的完整资料 
 * @description: 组合模式容器
 * @author: 阿星不是程序员
 **/
public class CompositeContainer<T> {
    
    private final Map<String, AbstractComposite> allCompositeInterfaceMap = new HashMap<>();
    //这是一个初始方法，用于获取容器中的所有的bean
    public void init(ConfigurableApplicationContext applicationEvent){
        //获取所有组合组件 从Spring容器中获取所有类型为AbstractComposite的Bean 返回Map格式：{beanName: beanInstance}
        Map<String, AbstractComposite> compositeInterfaceMap = applicationEvent.getBeansOfType(AbstractComposite.class);
        //将获取到的所有组件按type()方法分组
        Map<String, List<AbstractComposite>> collect = compositeInterfaceMap.values().stream().collect(Collectors.groupingBy(AbstractComposite::type));
        collect.forEach((k,v) -> {
            //构建组件树结构
            AbstractComposite root = build(v);
            //如果根节点存在，则执行业务逻辑
            if (Objects.nonNull(root)) {
                allCompositeInterfaceMap.put(k, root);
            }
        });
    }
    //执行树中的节点
    public void execute(String type,T param){
        AbstractComposite compositeInterface = Optional.ofNullable(allCompositeInterfaceMap.get(type))
                .orElseThrow(() -> new DaMaiFrameException(BaseCode.COMPOSITE_NOT_EXIST));
        compositeInterface.allExecute(param);
    }
    
    /**
     * 构建组件树的辅助方法。
     * @param groupedByTier 按层级组织的组件映射。
     * @param currentTier 当前处理的层级。
     */
    private static void buildTree(Map<Integer, Map<Integer, AbstractComposite>> groupedByTier, int currentTier) {
        Map<Integer, AbstractComposite> currentLevelComponents = groupedByTier.get(currentTier);
        Map<Integer, AbstractComposite> nextLevelComponents = groupedByTier.get(currentTier + 1);
        
        if (currentLevelComponents == null) {
            //当前层级没有组件时，直接返回
            return;
        }
        
        if (nextLevelComponents != null) {
            for (AbstractComposite child : nextLevelComponents.values()) {
                Integer parentOrder = child.executeParentOrder();
                if (parentOrder == null || parentOrder == 0) {
                    continue;
                }
                AbstractComposite parent = currentLevelComponents.get(parentOrder);
                if (parent != null) {
                    parent.add(child);
                }
            }
        }
        buildTree(groupedByTier, currentTier + 1);
    }
    
    /**
     * 根据提供的组件集合构建组件树，并返回根节点。
     * @param components 组件集合。
     * @return 根节点。
     */
    private static AbstractComposite build(Collection<AbstractComposite> components) {
        //按层级和执行顺序组织组件
        Map<Integer, Map<Integer, AbstractComposite>> groupedByTier = new TreeMap<>();
        
        for (AbstractComposite component : components) {
            groupedByTier.computeIfAbsent(component.executeTier(), k -> new HashMap<>(16))
                    .put(component.executeOrder(), component);
        }
        
        Integer minTier = groupedByTier.keySet().stream().min(Integer::compare).orElse(null);
        if (minTier == null) {
            return null;
        }
        
        buildTree(groupedByTier, minTier);
        
        return groupedByTier.get(minTier).values().stream()
                .filter(c -> c.executeParentOrder() == null || c.executeParentOrder() == 0)
                .findFirst()
                .orElse(null);
    }
}
